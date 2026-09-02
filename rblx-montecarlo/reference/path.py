"""One simulated path, decomposed into the four things that build it.
Jump days emit TWO points at the same x so the polyline is literally vertical."""
import numpy as np, json
S0, SH, C0 = 40.86, 0.710, 5.10
EVPS0 = (S0*SH-C0)/SH; CASH = C0/SH
T, N = 1.0, 252; DT = T/N; EARN = [41,110,165,228]; NU = 6.0; TSC = np.sqrt((NU-2)/NU)
sig, esd, lam, jm, js, cashT = 0.31, 0.210, 1.0, -0.05, 0.07, 5.6
anchor = 1.30*23.0/SH
FRAC_Q3 = 0.40
Q3 = FRAC_Q3*np.log(anchor/EVPS0)
mu = ((1-FRAC_Q3)*np.log(anchor/EVPS0) - lam*T*jm)/T + 0.5*sig**2

def draw(seed):
    r = np.random.default_rng(seed)
    drift = np.full(N, (mu-0.5*sig**2)*DT)
    diff  = sig*np.sqrt(DT)*(r.standard_t(NU, N)*TSC)
    earn  = np.zeros(N)
    for d in EARN: earn[d] = r.normal(Q3 if d == EARN[0] else 0.0, esd)
    cnt = r.poisson(lam*DT, N); hop = np.zeros(N)
    h = cnt > 0
    if h.any(): hop[h] = r.normal(jm*cnt[h], js*np.sqrt(cnt[h]))
    return drift, diff, earn, hop, np.where(h)[0]

# pick a seed with >=2 headline jumps and a big visible earnings gap
for seed in range(1, 400):
    drift, diff, earn, hop, hd = draw(seed)
    if len(hd) >= 2 and abs(earn).max() > 0.17 and abs(hop).max() > 0.07:
        break
print("seed", seed, "headline jumps at", hd.tolist(),
      "earnings moves", [f"{x:+.1%}" for x in earn[EARN]],
      "headline moves", [f"{x:+.1%}" for x in hop[hd]])

DIL = 0.025
shr = SH*(1 + DIL*np.arange(N+1)/N)
cash = (C0 + (cashT-C0)*np.arange(N+1)/N)


def series(parts, jumpdays):
    """cumulative price with a true vertical break on each jump day"""
    cont = sum(p for p in parts if p is not None)          # per-day log increment
    pts, v = [], 0.0
    px = lambda lv, i: round((EVPS0*SH*np.exp(lv) + cash[i])/shr[i], 3)
    pts.append([0.0, px(0.0, 0)])
    jd = dict(jumpdays)
    for d in range(N):
        v += cont[d]                                        # continuous part of the day
        pts.append([d+1, px(v, d+1)])
        if d in jd:                                         # then the gap, same x
            v += jd[d]
            pts.append([d+1, px(v, d+1)])
    return pts

ej = {d: earn[d] for d in EARN}
hj = {int(d): hop[d] for d in hd}
layers = [
 {"key":"drift","label":"1 · Deterministic drift","sub":"(μ − ½σ²)Δt — calibrated so the median lands on the anchor",
  "pts":series([drift], {})},
 {"key":"diff","label":"2 · + Student-t diffusion","sub":"σ√Δt · ε, continuous, no gaps",
  "pts":series([drift,diff], {})},
 {"key":"earn","label":"3 · + 4 scheduled earnings gaps","sub":"known dates, random size; the Q3 print also carries 40% of the repricing",
  "pts":series([drift,diff], ej)},
 {"key":"full","label":"4 · + Poisson headline jumps","sub":"random dates, negative mean — the finished path",
  "pts":series([drift,diff], {**ej, **hj})},
]
allv = [p[1] for L in layers for p in L["pts"]]
neg = [d for d in EARN if earn[d] < -0.15]
big = min(neg, key=lambda d: earn[d]) if neg else max(EARN, key=lambda d: abs(earn[d]))
out = {"layers":layers, "ymin":min(allv), "ymax":max(allv), "N":N,
       "earn_days":EARN, "hop_days":[int(x) for x in hd],
       "earn_moves":{str(d): float(earn[d]) for d in EARN},
       "hop_moves":{str(int(d)): float(hop[d]) for d in hd},
       "zoom_center":int(big), "zoom_move":float(earn[big]), "seed":seed}

# ---- measure panel: E[S_t] under P (each scenario) vs Q (risk-neutral) ----
SCN = {"bear":(0.70,18.0,0.39,0.230,2.0,-0.10,0.08,4.4),
       "base":(1.30,23.0,0.31,0.210,1.0,-0.05,0.07,5.6),
       "bull":(1.90,28.0,0.36,0.210,0.7, 0.02,0.08,6.2)}
wk = np.arange(0, N+1, 7)
means = {}
for i,(k,(f,m,sg,es,lm,j_m,j_s,cT)) in enumerate(SCN.items()):
    a = f*m/SH; q3k = FRAC_Q3*np.log(a/EVPS0)
    mu_k = ((1-FRAC_Q3)*np.log(a/EVPS0) - lm*T*j_m)/T + 0.5*sg**2
    r = np.random.default_rng(500+i); n = 40_000
    inc = (mu_k-0.5*sg**2)*DT + sg*np.sqrt(DT)*(r.standard_t(NU,size=(n,N))*TSC)
    for d in EARN: inc[:,d] += r.normal(q3k if d == EARN[0] else 0.0, es, n)
    c = r.poisson(lm*DT, size=(n,N)); hh = c>0
    if hh.any():
        kk = c[hh]; inc[hh] += r.normal(j_m*kk, j_s*np.sqrt(kk))
    ev = EVPS0*np.exp(np.concatenate([np.zeros((n,1)), np.cumsum(inc,1)],1))
    cs = C0 + (cT-C0)*np.arange(N+1)/N
    means[k] = np.round(((ev*SH+cs)/shr).mean(0)[wk], 3).tolist()
means["q"] = np.round(S0*np.exp(0.04*wk/N), 3).tolist()
means["weeks"] = wk.tolist()
out["measure"] = means
json.dump(out, open("path.json","w"))
print("E[S_T]: bear", means["bear"][-1], "base", means["base"][-1],
      "bull", means["bull"][-1], "| Q", means["q"][-1])
