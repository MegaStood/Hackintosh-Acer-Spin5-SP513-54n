import numpy as np, json, math
from math import gamma, pi, sqrt, exp
NU = 6.0; S = sqrt((NU-2)/NU)
def tpdf(x, nu=NU):
    t = x/S
    c = gamma((nu+1)/2)/(sqrt(nu*pi)*gamma(nu/2))
    return c*(1+t*t/nu)**(-(nu+1)/2)/S
def npdf(x): return exp(-x*x/2)/sqrt(2*pi)

xs = [i*0.05 for i in range(-120, 121)]           # -6..6 sigma
curves = {"x": xs, "t": [tpdf(x) for x in xs], "g": [npdf(x) for x in xs]}

# tail exceedance, both
R = np.random.default_rng(3); M = 4_000_000
tt = R.standard_t(NU, M)*S; gg = R.standard_normal(M)
rows = []
for k in (2,3,4,5,6):
    pg = 2*(1-0.5*(1+math.erf(k/sqrt(2))))
    pt = float((np.abs(tt) > k).mean())
    rows.append({"k":k, "g":pg, "t":pt, "ratio": pt/pg if pg>0 else float("nan")})
    print(f"P(|z|>{k}sd)  gauss {pg:.3e}   t(6) {pt:.3e}   ratio {pt/pg:>6.1f}x")

# --- how kurtosis decays with horizon, and where it actually lives ---
T, N = 1.0, 252; DT = T/N; EARN=[41,110,165,228]; TSC=S
S0, SH, C0 = 40.86, 0.710, 5.10; EVPS0 = (S0*SH-C0)/SH
def exk(x):
    d = x-x.mean(); return float((d**4).mean()/d.std()**4 - 3)
def skw(x):
    d = x-x.mean(); return float((d**3).mean()/d.std()**3)
FRAC_Q3 = 0.40
def sim(n, sig, esd, lam, jm, js, seed, q3=0.0):
    r = np.random.default_rng(seed)
    inc = -0.5*sig**2*DT + sig*np.sqrt(DT)*(r.standard_t(NU,size=(n,N))*TSC)
    for d in EARN: inc[:,d] += r.normal(q3 if d == EARN[0] else 0.0, esd, n)
    c = r.poisson(lam*DT, size=(n,N)); h = c>0
    if h.any():
        k = c[h]; inc[h] += r.normal(jm*k, js*np.sqrt(k))
    return inc
P = dict(sig=0.31, esd=0.210, lam=1.0, jm=-0.05, js=0.07, q3=FRAC_Q3*np.log(1.30*23.0/SH/EVPS0))
inc = sim(150_000, **P, seed=11)
hz = []
print("\nexcess kurtosis of the log-return, by horizon (base scenario)")
for lbl, d in [("1 day",1),("1 week",5),("1 month",21),("3 months",63),("6 months",126),("12 months",252)]:
    a = inc[:, :d].sum(1) if d>1 else inc[:,:1].ravel()
    hz.append({"h":lbl,"k":exk(a),"s":skw(a)})
    print(f"  {lbl:<12} skew {skw(a):>7.3f}   excess kurtosis {exk(a):>7.2f}")

# the blended mixture: where the real non-normality is
anch = {"bear":(0.70,18.0,0.39,0.230,2.0,-0.10,0.08,4.4,0.35),
        "base":(1.30,23.0,0.31,0.210,1.0,-0.05,0.07,5.6,0.45),
        "bull":(1.90,28.0,0.36,0.210,0.7, 0.02,0.08,6.2,0.20)}
parts, ws = [], []
for i,(k,(f,m,sg,es,lm,jm,js,cT,w)) in enumerate(anch.items()):
    a = f*m/SH
    q3 = FRAC_Q3*np.log(a/EVPS0)
    mu = ((1-FRAC_Q3)*np.log(a/EVPS0) - lm*T*jm)/T + 0.5*sg**2
    inc = sim(150_000, sig=sg, esd=es, lam=lm, jm=jm, js=js, seed=100+i, q3=q3) + mu*DT
    px = EVPS0*np.exp(inc.sum(1)) + cT/SH
    parts.append(np.log(px/S0)); ws.append(w)
r = np.random.default_rng(5); pick = r.choice(3, 300_000, p=ws)
mix = np.empty(300_000)
for i in range(3):
    msk = pick==i; mix[msk] = r.choice(parts[i], int(msk.sum()), replace=True)
PBEAR = dict(sig=0.39, esd=0.230, lam=2.0, jm=-0.10, js=0.08, q3=FRAC_Q3*np.log(0.70*18.0/SH/EVPS0))
KEEP = [c for c in range(N) if c != EARN[0]]        # exclude the scenario-signed Q3 gap day
d_base = sim(60_000, **P, seed=21)[:, KEEP].ravel()
d_bear = sim(60_000, **PBEAR, seed=22)[:, KEEP].ravel()
daily = {"base": {"skew": skw(d_base), "kurt": exk(d_base)},
         "bear": {"skew": skw(d_bear), "kurt": exk(d_bear)}}
print(f"daily all-days skew: base {daily['base']['skew']:+.3f}  bear {daily['bear']['skew']:+.3f}")
print(f"\nannual log-return, single scenario (base): skew {skw(parts[1]):+.3f}  ex.kurt {exk(parts[1]):+.2f}")
print(f"annual log-return, BLENDED mixture:        skew {skw(mix):+.3f}  ex.kurt {exk(mix):+.2f}")
json.dump({"curves":curves,"tails":rows,"horizon":hz,"daily":daily,
           "mix_skew":skw(mix),"mix_kurt":exk(mix),
           "base_skew":skw(parts[1]),"base_kurt":exk(parts[1])},
          open("tails.json","w"))
