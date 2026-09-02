"""
RBLX 12-month Monte Carlo, three fundamental scenarios.  v2 (post-review)

Key modelling choices
---------------------
1. The stochastic process is applied to TOTAL ENTERPRISE VALUE ($B); the share
   price is (EV_t + net cash_t) / shares_t. RBLX holds $5.1B net cash and still
   generates ~$1.1B FCF, so a plain GBM on the equity overstates the left tail.
   The cash cushion gives an INVERSE leverage effect: it damps equity volatility
   as the stock falls toward cash and amplifies it on rallies
   (equity vol = EV vol x EV/(EV+cash), ~x0.82 at spot), which contributes to
   the positive skew of the terminal equity distribution.
2. Shares outstanding grow 2.5%/yr (SBC-driven net issuance after buybacks).
3. Earnings gaps sized to the realized prints (-18% and -29% on the last two):
   EV-space sd 0.21-0.23 (~17-19% one-sigma in equity terms at spot), with the
   diffusion sigma trimmed so each scenario keeps its total variance budget.
4. Drift is calibrated so the MEDIAN terminal EV equals each scenario's
   fundamental anchor (FY27 FCF x exit multiple), Poisson compensator removed.
5. Touch probabilities use a Brownian-bridge intraday correction on the
   diffusion component (gaps checked at their endpoints); the daily-close-only
   numbers run about 2pp lower.
"""
import numpy as np, json, os

RNG = np.random.default_rng(20260901)
OUT = os.path.dirname(os.path.abspath(__file__))

S0        = 40.86      # NYSE close 2026-08-31
SHARES0   = 0.710      # bn  (29.03bn mcap / 40.86)
NET_CASH0 = 5.10       # $bn (6.1 cash+investments less 1.0 debt, 6/30/26)
DIL       = 0.025      # net share issuance over the 12 months (post-buyback)
FRAC_Q3   = 0.40       # share of each scenario's log-repricing realised AT the Q3 print (day 41)
EV0       = S0 * SHARES0 - NET_CASH0         # 23.91bn total EV
EVPS0     = EV0 / SHARES0
T, N      = 1.0, 252
DT        = T / N
EARN      = [41, 110, 165, 228]              # ~Oct-29, Feb, Apr-30, Jul-30
NU        = 6.0
TSC       = np.sqrt((NU - 2) / NU)
GRID      = np.r_[np.arange(0, N, 5), N]     # weekly band grid incl. day 252

SCEN = {
 "bear": dict(prob=0.35, fcf27=0.70, mult=18.0, sigma=0.39, cashT=4.4,
              earn_sd=0.230, lam=2.0, jm=-0.10, js=0.08,
              name="Structural impairment"),
 "base": dict(prob=0.45, fcf27=1.30, mult=23.0, sigma=0.31, cashT=5.6,
              earn_sd=0.210, lam=1.0, jm=-0.05, js=0.07,
              name="Trough, then stabilisation"),
 "bull": dict(prob=0.20, fcf27=1.90, mult=28.0, sigma=0.36, cashT=6.2,
              earn_sd=0.210, lam=0.7, jm=0.02, js=0.08,
              name="Re-acceleration on ads + verified base"),
}
# sigma/earn_sd act on EV; the cash cushion damps them ~x0.82 in equity terms
# at spot. Totals per scenario match the v1 variance budget (blend ~0.65 1y
# equity log-vol); the split now puts realized-print-sized risk on the 4 gaps.
for s in SCEN.values():
    s["ev_anchor"] = s["fcf27"] * s["mult"]                      # $B
    s["px_anchor"] = (s["ev_anchor"] + s["cashT"]) / (SHARES0 * (1 + DIL))
    s["lnA"] = np.log(s["ev_anchor"] / EV0)
    s["q3_mean"] = FRAC_Q3 * s["lnA"]                 # scenario-signed Q3 gap mean
    s["mu"] = ((1 - FRAC_Q3) * s["lnA"] - s["lam"] * T * s["jm"]) / T \
              + 0.5 * s["sigma"] ** 2

DAYS    = np.arange(N + 1)
SHARES_T = SHARES0 * (1 + DIL * DAYS / N)          # bn, per grid day
def simulate(s, npaths=200_000, chunk=25_000):
    cash_t = NET_CASH0 + (s["cashT"] - NET_CASH0) * DAYS / N     # $B
    term, rmin, p3, p6, bands = [], [], [], [], []
    touch = {25.0: [], 33.88: []}
    sig2dt = s["sigma"] ** 2 * DT
    # EV-space log barriers per day (barrier price -> required EV)
    bar_log = {b: np.log(np.maximum(b * SHARES_T - cash_t, 1e-9)) - np.log(EV0)
               for b in touch}
    for start in range(0, npaths, chunk):
        n = min(chunk, npaths - start)
        z = RNG.standard_t(NU, size=(n, N)) * TSC
        cont = (s["mu"] - 0.5 * s["sigma"] ** 2) * DT + s["sigma"] * np.sqrt(DT) * z
        jump = np.zeros((n, N))
        for d in EARN:
            jump[:, d] += RNG.normal(s["q3_mean"] if d == EARN[0] else 0.0, s["earn_sd"], n)
        cnt = RNG.poisson(s["lam"] * DT, size=(n, N))
        h = cnt > 0
        if h.any():
            k = cnt[h]
            jump[h] += RNG.normal(s["jm"] * k, s["js"] * np.sqrt(k))
        L = np.concatenate([np.zeros((n, 1)), np.cumsum(cont + jump, 1)], 1)
        px = (EV0 * np.exp(L) + cash_t) / SHARES_T
        # interval minimum of the diffusion leg via the Brownian-bridge minimum law
        o_ = L[:, :-1] + jump; c_ = L[:, 1:]
        U = RNG.uniform(1e-12, 1.0, size=o_.shape)
        m_ = 0.5 * (o_ + c_ - np.sqrt((o_ - c_) ** 2 - 2.0 * sig2dt * np.log(U)))
        px_min_intra = (EV0 * np.exp(m_) + cash_t[1:]) / SHARES_T[1:]
        rmin_path = np.minimum(px.min(1), px_min_intra.min(1))
        term.append(px[:, -1]); rmin.append(rmin_path)
        p3.append(px[:, 63]); p6.append(px[:, 126])
        bands.append(px[:, GRID].astype(np.float32))
        # ---- Brownian-bridge touch on the diffusion leg (gap = overnight) ----
        open_ = L[:, :-1] + jump                    # log EV after day-d gap
        close = L[:, 1:]
        for b, blog in bar_log.items():
            b_open = blog[1:]                       # day-d barrier for both legs
            a0 = open_ - b_open; a1 = close - b_open
            hit_ep = (a0 <= 0) | (a1 <= 0)
            p_cross = np.exp(np.clip(-2.0 * a0 * a1 / sig2dt, -745, 0))
            p_cross = np.where(hit_ep, 1.0, p_cross)
            p_no = np.prod(1.0 - np.minimum(p_cross, 1.0), axis=1)
            start_hit = px[:, 0] <= b
            touch[b].append(np.where(start_hit, 1.0, 1.0 - p_no))
    return (np.concatenate(term), np.concatenate(rmin),
            np.concatenate(p3), np.concatenate(p6), np.concatenate(bands),
            {b: float(np.concatenate(v).mean()) for b, v in touch.items()})

PCTS = [1, 5, 10, 25, 50, 75, 90, 95, 99]
def stats(x, s0=S0):
    d = {f"p{p}": float(np.percentile(x, p)) for p in PCTS}
    q5 = np.percentile(x, 5)
    d |= dict(mean=float(x.mean()),
              exp_ret=float(x.mean()/s0-1), med_ret=float(np.median(x)/s0-1),
              P_gain=float((x > s0).mean()),
              P_gt_4832=float((x > 48.32).mean()),
              P_gt_70=float((x > 70).mean()),
              P_gt_100=float((x > 100).mean()),
              P_lt_3388=float((x < 33.88).mean()),
              P_lt_25=float((x < 25).mean()),
              P_lt_20=float((x < 20).mean()),
              P_halved=float((x < s0/2).mean()),
              P_doubled=float((x > 2*s0).mean()),
              VaR95=float(q5/s0-1), CVaR95=float(x[x <= q5].mean()/s0-1),
              realised_vol=float(np.std(np.log(x/s0))))
    return d

BINS, RANGE = 90, (0.0, 180.0)
out = dict(spot=S0, asof="2026-08-31", npaths=200_000, ev0=EV0, evps0=EVPS0,
           cashps0=NET_CASH0/SHARES0, shares=SHARES0, dilution=DIL,
           hist_range=list(RANGE), hist_bins=BINS,
           scenarios={}, weeks=[int(x) for x in GRID])
terms, ws = [], []
for k, s in SCEN.items():
    t_, rmin, p3, p6, bnd, tch = simulate(s)
    st = stats(t_)
    st |= dict(prob=s["prob"], name=s["name"], fcf27=s["fcf27"], mult=s["mult"],
               px_anchor=s["px_anchor"], ev_anchor=s["ev_anchor"],
               sigma=s["sigma"], earn_sd=s["earn_sd"], mu=s["mu"], lam=s["lam"],
               P_touch_25=tch[25.0],
               P_touch_3388=tch[33.88],
               med_trough=float(np.median(rmin)/S0-1),
               q3_mean=s["q3_mean"], frac_q3=FRAC_Q3,
               h3m=stats(p3), h6m=stats(p6),
               bands={f"p{p}": np.percentile(bnd, p, 0).round(2).tolist()
                      for p in (5, 25, 50, 75, 95)},
               hist=np.histogram(np.clip(t_, *RANGE), BINS, RANGE)[0].tolist())
    out["scenarios"][k] = st
    terms.append(t_); ws.append(s["prob"])
    print(f"{k:5s} anchor={s['px_anchor']:6.2f} med={st['p50']:6.2f} "
          f"p5={st['p5']:5.2f} p95={st['p95']:6.2f} P(gain)={st['P_gain']:5.1%} "
          f"touch3388={tch[33.88]:.1%} q3gap={s['q3_mean']:+.3f} 3m={st['h3m']['p50']:.2f}")

NB = 300_000
pick = RNG.choice(3, NB, p=ws)
keys = list(SCEN)
mix = np.empty(NB)
for i in range(3):
    m = pick == i
    mix[m] = RNG.choice(terms[i], int(m.sum()), replace=True)
out["blend"] = stats(mix) | dict(hist=np.histogram(np.clip(mix, *RANGE), BINS, RANGE)[0].tolist())

# ---- weights sensitivity: exact weighted quantiles over pooled terminals ----
def wstats(weights):
    x = np.concatenate(terms)
    w = np.concatenate([np.full(len(t), wi / len(t)) for t, wi in zip(terms, weights)])
    o = np.argsort(x); x, w = x[o], w[o]
    cdf = np.cumsum(w); cdf /= cdf[-1]
    med = float(x[np.searchsorted(cdf, 0.5)])
    def pgt(v): return float(w[x > v].sum() / w.sum())
    return dict(median=med, med_ret=med/S0-1, P_gain=pgt(S0),
                P_gt_4832=pgt(48.32), P_halved=1-pgt(S0/2)-float(w[x==S0/2].sum()))
WSETS = [("35 / 45 / 20 (this report)", (0.35, 0.45, 0.20)),
         ("33 / 33 / 33", (1/3, 1/3, 1/3)),
         ("25 / 45 / 30", (0.25, 0.45, 0.30)),
         ("40 / 45 / 15", (0.40, 0.45, 0.15)),
         ("45 / 40 / 15", (0.45, 0.40, 0.15)),
         ("50 / 35 / 15", (0.50, 0.35, 0.15))]
out["wsens"] = [dict(label=lb, **wstats(w)) for lb, w in WSETS]

iv = 0.57
rn = S0*np.exp((0.04 - 0.5*iv**2)*T + iv*RNG.standard_normal(300_000))
out["risk_neutral"] = stats(rn) | dict(iv=iv)
LEV_SPOT = EV0 / (S0 * SHARES0)                       # ~0.824
iv_evt = float(np.sqrt(iv**2 + 4 * (SCEN["base"]["earn_sd"] * LEV_SPOT) ** 2))
rn2 = S0*np.exp((0.04 - 0.5*iv_evt**2)*T + iv_evt*RNG.standard_normal(300_000))
out["risk_neutral_evt"] = stats(rn2) | dict(iv=iv_evt)

# ---- sensitivity of the blend to total volatility: scale ALL within-scenario
#      shocks (diffusion, gaps, headline jump size) by one factor f ----
def blend_at(f, npaths=60_000):
    saved = {k: (v["sigma"], v["earn_sd"], v["js"], v["mu"]) for k, v in SCEN.items()}
    parts = []
    for k, v in SCEN.items():
        v["sigma"], v["earn_sd"], v["js"] = saved[k][0]*f, saved[k][1]*f, saved[k][2]*f
        v["mu"] = ((1 - FRAC_Q3) * v["lnA"] - v["lam"] * T * v["jm"]) / T + 0.5 * v["sigma"] ** 2
        parts.append(simulate(v, npaths=npaths)[0])
    for k, v in SCEN.items():
        v["sigma"], v["earn_sd"], v["js"], v["mu"] = saved[k]
    x = np.concatenate(parts)
    w = np.concatenate([np.full(len(p), wi/len(p)) for p, wi in zip(parts, ws)])
    o = np.argsort(x); x, w = x[o], w[o]; cdf = np.cumsum(w); cdf /= cdf[-1]
    q = lambda p: float(x[np.searchsorted(cdf, p)])
    lv = float(np.sqrt(np.sum(w*(np.log(x/S0) - np.sum(w*np.log(x/S0))/w.sum())**2)/w.sum()))
    return dict(f=f, logvol=lv, p5=q(.05), p50=q(.5), p95=q(.95),
                P_gain=float(w[x > S0].sum()/w.sum()), P_halved=float(w[x < S0/2].sum()/w.sum()))
out["vsens"] = [blend_at(f) for f in (0.75, 1.0, 1.25)]

json.dump(out, open(f"{OUT}/results.json", "w"), indent=1)
b = out["blend"]
print(f"\nBLEND med={b['p50']:.2f} ({b['med_ret']:+.1%}) mean={b['mean']:.2f} "
      f"p5={b['p5']:.2f} p95={b['p95']:.2f} P(gain)={b['P_gain']:.1%} "
      f"P(>48.32)={b['P_gt_4832']:.1%} eqvol={b['realised_vol']:.3f}")
for r in out["vsens"]:
    print(f"  vol x{r['f']:.2f}: logvol={r['logvol']:.3f} p5={r['p5']:.2f} med={r['p50']:.2f} "
          f"p95={r['p95']:.2f} P(gain)={r['P_gain']:.1%} P(halved)={r['P_halved']:.1%}")
for r in out["wsens"]:
    print(f"  {r['label']:26s} med={r['median']:6.2f} ({r['med_ret']:+6.1%}) "
          f"P(gain)={r['P_gain']:5.1%} P(>48.32)={r['P_gt_4832']:5.1%} P(halved)={r['P_halved']:5.1%}")
