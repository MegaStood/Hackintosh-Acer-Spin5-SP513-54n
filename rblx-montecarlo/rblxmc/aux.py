"""Auxiliary chart data for the report (NumPy, small, main rank only).

``write_tails``  — Student-t vs Gaussian densities, tail-exceedance table, kurtosis by
                   horizon, daily skew on a common basis, mixture skew/kurtosis.
``write_path``   — one simulated year decomposed into drift / diffusion / gaps / jumps
                   (with duplicate points at each gap so the polyline is vertical), the
                   zoom target, and the expected-price-under-P-vs-Q panel.

Both re-derive every parameter from the ModelConfig so they can never drift from the
main simulation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .config import ModelConfig, Scenario


def _exk(x):
    d = x - x.mean()
    return float((d ** 4).mean() / d.std() ** 4 - 3)


def _skw(x):
    d = x - x.mean()
    return float((d ** 3).mean() / d.std() ** 3)


def _sim_inc(cfg: ModelConfig, sc: Scenario, n: int, seed: int, *, q3: float, drift: float = 0.0):
    """Daily log increments of EV for one scenario (drift-free unless ``drift`` given)."""
    N, dt, nu = cfg.n_steps, cfg.dt, cfg.nu
    tsc = math.sqrt((nu - 2) / nu)
    r = np.random.default_rng(seed)
    inc = (drift - 0.5 * sc.sigma ** 2) * dt + sc.sigma * math.sqrt(dt) * (r.standard_t(nu, size=(n, N)) * tsc)
    for i, d in enumerate(cfg.earn_days):
        inc[:, d] += r.normal(q3 if i == 0 else 0.0, sc.earn_sd, n)
    c = r.poisson(sc.lam * dt, size=(n, N))
    h = c > 0
    if h.any():
        k = c[h]
        inc[h] += r.normal(sc.jm * k, sc.js * np.sqrt(k))
    return inc


def _scen(cfg, key) -> Scenario:
    return next(s for s in cfg.scenarios if s.key == key)


# ---------------------------------------------------------------------------- tails
def write_tails(cfg: ModelConfig, outdir: Path, seed: int = 3) -> dict:
    nu = float(cfg.nu)
    S = math.sqrt((nu - 2) / nu)

    def tpdf(x):
        t = x / S
        c = math.gamma((nu + 1) / 2) / (math.sqrt(nu * math.pi) * math.gamma(nu / 2))
        return c * (1 + t * t / nu) ** (-(nu + 1) / 2) / S

    def npdf(x):
        return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)

    xs = [i * 0.05 for i in range(-120, 121)]
    curves = {"x": xs, "t": [tpdf(x) for x in xs], "g": [npdf(x) for x in xs]}

    R = np.random.default_rng(seed)
    M = 4_000_000
    tt = R.standard_t(nu, M) * S
    rows = []
    for k in (2, 3, 4, 5, 6):
        pg = 2 * (1 - 0.5 * (1 + math.erf(k / math.sqrt(2))))
        pt = float((np.abs(tt) > k).mean())
        rows.append({"k": k, "g": pg, "t": pt, "ratio": pt / pg})
    del tt

    N = cfg.n_steps
    base, bear = _scen(cfg, "base"), _scen(cfg, "bear")
    db, dbear = cfg.derive(base), cfg.derive(bear)

    inc = _sim_inc(cfg, base, 150_000, 11, q3=db.q3_mean)
    hz = []
    for lbl, d in [("1 day", 1), ("1 week", 5), ("1 month", 21), ("3 months", 63),
                   ("6 months", 126), ("12 months", N)]:
        a = inc[:, :d].sum(1) if d > 1 else inc[:, :1].ravel()
        hz.append({"h": lbl, "k": _exk(a), "s": _skw(a)})
    del inc

    # mixture of annual equity log-returns
    parts, ws = [], []
    shares_T = cfg.shares0 * (1 + cfg.dilution)
    for i, sc in enumerate(cfg.scenarios):
        der = cfg.derive(sc)
        inc = _sim_inc(cfg, sc, 150_000, 100 + i, q3=der.q3_mean, drift=der.mu)
        px = (cfg.ev0 * np.exp(inc.sum(1)) + sc.cash_t) / shares_T
        parts.append(np.log(px / cfg.spot))
        ws.append(sc.prob)
        del inc
    r = np.random.default_rng(5)
    pick = r.choice(len(parts), 300_000, p=ws)
    mix = np.empty(300_000)
    for i in range(len(parts)):
        m = pick == i
        mix[m] = r.choice(parts[i], int(m.sum()), replace=True)

    keep = [c for c in range(N) if c != cfg.earn_days[0]]      # drop the scenario-signed print
    d_base = _sim_inc(cfg, base, 60_000, 21, q3=db.q3_mean)[:, keep].ravel()
    d_bear = _sim_inc(cfg, bear, 60_000, 22, q3=dbear.q3_mean)[:, keep].ravel()
    daily = {"base": {"skew": _skw(d_base), "kurt": _exk(d_base)},
             "bear": {"skew": _skw(d_bear), "kurt": _exk(d_bear)}}
    ib = [s.key for s in cfg.scenarios].index("base")
    out = {"curves": curves, "tails": rows, "horizon": hz, "daily": daily,
           "mix_skew": _skw(mix), "mix_kurt": _exk(mix),
           "base_skew": _skw(parts[ib]), "base_kurt": _exk(parts[ib])}
    Path(outdir, "tails.json").write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------------------- path
def write_path(cfg: ModelConfig, outdir: Path, max_seed: int = 400) -> dict:
    N, dt, nu = cfg.n_steps, cfg.dt, cfg.nu
    tsc = math.sqrt((nu - 2) / nu)
    EARN = list(cfg.earn_days)
    base = _scen(cfg, "base")
    db = cfg.derive(base)

    def draw(seed):
        r = np.random.default_rng(seed)
        drift = np.full(N, (db.mu - 0.5 * base.sigma ** 2) * dt)
        diff = base.sigma * math.sqrt(dt) * (r.standard_t(nu, N) * tsc)
        earn = np.zeros(N)
        for i, d in enumerate(EARN):
            earn[d] = r.normal(db.q3_mean if i == 0 else 0.0, base.earn_sd)
        cnt = r.poisson(base.lam * dt, N)
        hop = np.zeros(N)
        h = cnt > 0
        if h.any():
            hop[h] = r.normal(base.jm * cnt[h], base.js * np.sqrt(cnt[h]))
        return drift, diff, earn, hop, np.where(h)[0]

    # a seed with >=2 headline jumps, a big visible earnings gap and a visible headline move
    for seed in range(1, max_seed):
        drift, diff, earn, hop, hd = draw(seed)
        if len(hd) >= 2 and np.abs(earn).max() > 0.17 and np.abs(hop).max() > 0.07:
            break

    days = np.arange(N + 1)
    shr = cfg.shares0 * (1 + cfg.dilution * days / N)
    cash = cfg.net_cash0 + (base.cash_t - cfg.net_cash0) * days / N

    def series(parts, jumpdays):
        cont = sum(parts)
        pts, v = [], 0.0
        px = lambda lv, i: round(float((cfg.ev0 * math.exp(lv) + cash[i]) / shr[i]), 3)
        pts.append([0.0, px(0.0, 0)])
        for d in range(N):
            v += cont[d]
            pts.append([d + 1, px(v, d + 1)])
            if d in jumpdays:
                v += jumpdays[d]
                pts.append([d + 1, px(v, d + 1)])
        return pts

    ej = {d: float(earn[d]) for d in EARN}
    hj = {int(d): float(hop[d]) for d in hd}
    pct = int(round(cfg.frac_q3 * 100))
    layers = [
        {"key": "drift", "label": "1 · Deterministic drift",
         "sub": "(μ − ½σ²)Δt — calibrated so the median lands on the anchor",
         "pts": series([drift], {})},
        {"key": "diff", "label": "2 · + Student-t diffusion",
         "sub": "σ√Δt · ε, continuous, no gaps", "pts": series([drift, diff], {})},
        {"key": "earn", "label": f"3 · + {len(EARN)} scheduled earnings gaps",
         "sub": f"known dates, random size; the first print also carries {pct}% of the repricing",
         "pts": series([drift, diff], ej)},
        {"key": "full", "label": "4 · + Poisson headline jumps",
         "sub": "random dates, negative mean — the finished path",
         "pts": series([drift, diff], {**ej, **hj})},
    ]
    allv = [p[1] for L in layers for p in L["pts"]]
    neg = [d for d in EARN if earn[d] < -0.15]
    big = min(neg, key=lambda d: earn[d]) if neg else max(EARN, key=lambda d: abs(earn[d]))
    out = {"layers": layers, "ymin": min(allv), "ymax": max(allv), "N": N,
           "earn_days": EARN, "hop_days": [int(x) for x in hd],
           "earn_moves": {str(d): float(earn[d]) for d in EARN},
           "hop_moves": {str(int(d)): float(hop[d]) for d in hd},
           "zoom_center": int(big), "zoom_move": float(earn[big]), "seed": int(seed)}

    # expected price under P per scenario vs the risk-neutral drift
    wk = np.arange(0, N + 1, 7)
    means = {}
    for i, sc in enumerate(cfg.scenarios):
        der = cfg.derive(sc)
        inc = _sim_inc(cfg, sc, 40_000, 500 + i, q3=der.q3_mean, drift=der.mu)
        ev = cfg.ev0 * np.exp(np.concatenate([np.zeros((inc.shape[0], 1)), np.cumsum(inc, 1)], 1))
        cs = cfg.net_cash0 + (sc.cash_t - cfg.net_cash0) * days / N
        means[sc.key] = np.round(((ev + cs) / shr).mean(0)[wk], 3).tolist()
        del inc, ev
    means["q"] = np.round(cfg.spot * np.exp(cfg.rf * wk / N), 3).tolist()
    means["weeks"] = wk.tolist()
    out["measure"] = means
    Path(outdir, "path.json").write_text(json.dumps(out))
    return out
