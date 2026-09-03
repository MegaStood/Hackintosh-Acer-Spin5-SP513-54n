"""Rolling out-of-sample coverage test of the within-scenario engine.

    python -m rblxmc.backtest --csv data/RBLX.csv --config configs/rblx_calibrated.toml \
        --horizons 5 21 63 --step 5 --paths 20000

At every ``step``-th trading day the engine (base-scenario shock parameters, zero drift,
GARCH variance filtered from the data up to that day, the real print dates inside the
window) simulates the distribution of the h-day log return; the realised return's
probability-integral-transform (PIT) is recorded.  If the engine is well calibrated the
PITs are uniform: the Kolmogorov-Smirnov statistic is small and the 50 / 90 / 98% bands
cover about 50 / 90 / 98% of outcomes.

This tests the SHAPE and the VOLATILITY of the engine.  It cannot test the scenario
anchors or their weights, which are forward-looking judgments.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import torch

from .calibrate import detect_earnings_days, garch_filter, load_prices_csv, load_prices_yf
from .config import ModelConfig
from .engine import ShockParams, horizon_log_returns


def ks_uniform(u: np.ndarray) -> tuple[float, float]:
    """KS statistic of u against U(0,1) and its asymptotic p-value."""
    u = np.sort(u)
    n = len(u)
    ecdf_hi = np.arange(1, n + 1) / n
    ecdf_lo = np.arange(0, n) / n
    d = float(max(np.max(ecdf_hi - u), np.max(u - ecdf_lo)))
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, float(min(max(p, 0.0), 1.0))


@dataclass
class HorizonResult:
    horizon: int
    n: int
    ks: float
    ks_p: float
    cover50: float
    cover90: float
    cover98: float
    mean_pit: float
    pit_hist: list[int]


def run_backtest(cfg: ModelConfig, dates: list[date], px: np.ndarray, *, horizons=(5, 21, 63),
                 step: int = 5, paths: int = 20_000, device="cpu", earn_idx=None,
                 seed: int = 1, drift_window: int = 0) -> dict:
    r = np.diff(np.log(px))
    n = len(r)
    base = next(s for s in cfg.scenarios if s.key == "base")
    # the backtest runs in EQUITY space: what the engine calls EV vol maps to equity vol by lev
    lev = cfg.lev_spot
    p = ShockParams(base.sigma * lev, cfg.nu, base.earn_sd * lev, cfg.gap_nu, base.lam,
                    base.jm * lev, base.js * lev, cfg.garch_alpha, cfg.garch_beta, cfg.garch_gamma)
    if earn_idx is None:
        earn_idx = detect_earnings_days(dates, r)
    earn_set = set(earn_idx)
    vbar = (p.sigma ** 2) * cfg.dt
    h = garch_filter(r - r.mean(), p.alpha, p.beta, p.gamma, vbar) if (p.alpha or p.beta or p.gamma) else np.full(n, vbar)

    H = max(horizons)
    pits = {hz: [] for hz in horizons}
    starts = list(range(252, n - H, step))
    for j, i in enumerate(starts):
        offsets = [k - i for k in range(i, i + H) if k in earn_set]
        drift = float(r[i - drift_window:i].mean() * 252) if drift_window else 0.0
        sims = horizon_log_returns(p, paths, list(horizons), cfg.dt, earn_offsets=offsets,
                                   drift=drift, h0=float(h[i]), seed=seed * 100_003 + j, device=device)
        for hz in horizons:
            realised = float(r[i:i + hz].sum())
            s = sims[hz].numpy()
            pits[hz].append(float((s <= realised).mean()))

    results = []
    for hz in horizons:
        u = np.array(pits[hz])
        d, pval = ks_uniform(u)
        inside = lambda lo, hi: float(((u >= lo) & (u <= hi)).mean())
        results.append(HorizonResult(hz, len(u), d, pval, inside(.25, .75), inside(.05, .95),
                                     inside(.01, .99), float(u.mean()),
                                     np.histogram(u, 10, (0, 1))[0].tolist()))
    return dict(n_starts=len(starts), horizons=[r.__dict__ for r in results],
                params=p.__dict__, earn_days_used=len(earn_idx))


def format_results(res: dict) -> str:
    out = [f"{'horizon':>8} {'n':>5} {'KS':>7} {'p':>7} {'in 50%':>7} {'in 90%':>7} {'in 98%':>7} {'mean PIT':>9}",
           "-" * 64]
    for h in res["horizons"]:
        out.append(f"{h['horizon']:>6}d {h['n']:>5} {h['ks']:>7.3f} {h['ks_p']:>7.3f} "
                   f"{h['cover50']:>7.1%} {h['cover90']:>7.1%} {h['cover98']:>7.1%} {h['mean_pit']:>9.3f}")
    out.append("target:            small   >0.05    50.0%   90.0%   98.0%     0.500")
    return "\n".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Rolling PIT / coverage backtest of the rblxmc engine")
    ap.add_argument("--config", required=True)
    ap.add_argument("--csv")
    ap.add_argument("--ticker")
    ap.add_argument("--start", default="2021-06-01")
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 21, 63])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--paths", type=int, default=20_000)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--drift-window", type=int, default=0, help="trailing days for a drift estimate (0 = zero drift)")
    ap.add_argument("--out", default="out/backtest.json")
    args = ap.parse_args(argv)
    cfg = ModelConfig.load(args.config)
    dates, px = load_prices_csv(args.csv) if args.csv else load_prices_yf(args.ticker, args.start)
    res = run_backtest(cfg, dates, px, horizons=tuple(args.horizons), step=args.step, paths=args.paths,
                       device=args.device, drift_window=args.drift_window)
    print(format_results(res))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
