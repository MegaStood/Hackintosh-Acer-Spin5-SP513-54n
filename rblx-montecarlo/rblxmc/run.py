"""Command-line entry point.

Single machine (CPU or one GPU)::

    python -m rblxmc.run --config configs/rblx_2026-09.toml --paths 2000000 --out out/

Two DGX Sparks (one process per node, NCCL over the ConnectX link)::

    torchrun --nnodes=2 --nproc_per_node=1 --node_rank=0 --master_addr=<spark-1 ip> \
             --master_port=29500 -m rblxmc.run --config ... --paths 20000000 --out out/

The main rank writes ``results.json``, then (unless ``--no-aux``) the auxiliary chart
data and the HTML report.
"""
from __future__ import annotations

import argparse
import json
import runpy
import sys
import time
from pathlib import Path

import numpy as np
import torch

from . import distributed as D
from .config import ModelConfig
from .engine import SimOutput, merge, simulate
from .stats import (band_percentiles, histogram, mixture_sample, risk_neutral,
                    summary, touch_key, weighted_stats)


def _log(ctx: D.Ctx, msg: str) -> None:
    if ctx.main:
        print(msg, flush=True)


def run_scenario(cfg, sc, der, n_total, ctx, args, sc_index) -> SimOutput | None:
    n_local = D.split(n_total, ctx)
    t0 = time.time()
    out = simulate(cfg, sc, der, n_local, chunk=args.chunk, device=ctx.device,
                   dtype=torch.float64 if args.dtype == "float64" else torch.float32,
                   seed=args.seed, rank=ctx.rank, sc_index=sc_index,
                   band_keep=max(1, args.band_keep // ctx.world))
    parts = D.gather(out, ctx)
    if not ctx.main:
        return None
    merged = merge(parts)
    _log(ctx, f"  {sc.key:5s} {merged.n:>10,d} paths  {time.time()-t0:6.1f}s")
    return merged


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Three-scenario jump-diffusion Monte Carlo")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--paths", type=int, default=200_000, help="paths per scenario (total across ranks)")
    ap.add_argument("--vsens-paths", type=int, default=60_000)
    ap.add_argument("--chunk", type=int, default=0, help="paths per batch (0 = 1M on CUDA, 250k on CPU)")
    ap.add_argument("--band-keep", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=20260901)
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    ap.add_argument("--dtype", default="float32", choices=("float32", "float64"))
    ap.add_argument("--no-aux", action="store_true", help="skip tails/path data and the HTML report")
    args = ap.parse_args(argv)

    ctx = D.init(args.device)
    if args.chunk <= 0:
        args.chunk = 1_000_000 if ctx.device.type == "cuda" else 250_000
    cfg = ModelConfig.load(args.config)
    outdir = Path(args.out)
    if ctx.main:
        outdir.mkdir(parents=True, exist_ok=True)
    _log(ctx, f"device={ctx.device} world={ctx.world} dtype={args.dtype} paths/scenario={args.paths:,}")

    # ------------------------------------------------------------ main simulation
    scen_out: dict[str, SimOutput] = {}
    for i, sc in enumerate(cfg.scenarios):
        der = cfg.derive(sc)
        res = run_scenario(cfg, sc, der, args.paths, ctx, args, i)
        if ctx.main:
            scen_out[sc.key] = res

    # ------------------------------------------------------------ volatility sensitivity
    vsens_terms: dict[float, list[np.ndarray]] = {}
    for f in cfg.vsens_factors:
        parts = []
        for i, sc in enumerate(cfg.scenarios):
            scf = cfg.scaled(sc, f)
            res = run_scenario(cfg, scf, cfg.derive(scf), args.vsens_paths, ctx, args, 100 + i)
            if ctx.main:
                parts.append(res.term.numpy().astype(np.float64))
        if ctx.main:
            vsens_terms[f] = parts

    if not ctx.main:
        D.finalize(ctx)
        return

    # ------------------------------------------------------------ statistics (main rank)
    rng = np.random.default_rng(args.seed)
    spot, sa = cfg.spot, cfg.street_avg
    out = dict(spot=spot, asof=cfg.asof, npaths=args.paths, ev0=cfg.ev0, evps0=cfg.evps0,
               cashps0=cfg.net_cash0 / cfg.shares0, shares=cfg.shares0, dilution=cfg.dilution,
               frac_q3=cfg.frac_q3, hist_range=list(cfg.hist_range), hist_bins=cfg.hist_bins,
               weeks=cfg.grid, scenarios={}, config=cfg.to_dict())
    terms, weights = [], []
    for sc in cfg.scenarios:
        r = scen_out[sc.key]
        der = cfg.derive(sc)
        term = r.term.numpy().astype(np.float64)
        st = summary(term, spot, sa)
        st.update(prob=sc.prob, name=sc.name, fcf27=sc.fcf27, mult=sc.mult,
                  px_anchor=der.px_anchor, ev_anchor=der.ev_anchor, sigma=sc.sigma,
                  earn_sd=sc.earn_sd, mu=der.mu, lam=sc.lam, q3_mean=der.q3_mean, frac_q3=cfg.frac_q3,
                  gap_means=list(der.gap_means), schedule=list(cfg.schedule),
                  bookings27=sc.bookings27, fcf_margin27=sc.fcf_margin27, lit_scale=sc.lit_scale,
                  factors=cfg.factor_summary(sc),
                  med_trough=float(np.median(r.rmin.numpy()) / spot - 1),
                  h3m=summary(r.p3.numpy(), spot, sa), h6m=summary(r.p6.numpy(), spot, sa),
                  bands=band_percentiles(r.bands.numpy()),
                  hist=histogram(term, cfg.hist_bins, cfg.hist_range))
        for b, tp in r.touch.items():
            st[touch_key(b)] = float(tp.numpy().mean())
        out["scenarios"][sc.key] = st
        terms.append(term)
        weights.append(sc.prob)
        print(f"{sc.key:5s} anchor={der.px_anchor:6.2f} median={st['p50']:6.2f} "
              f"p5={st['p5']:5.2f} p95={st['p95']:6.2f} P(gain)={st['P_gain']:5.1%}")

    mix = mixture_sample(terms, weights, 300_000, rng)
    out["blend"] = summary(mix, spot, sa) | {"hist": histogram(mix, cfg.hist_bins, cfg.hist_range)}
    out["wsens"] = [dict(label=lb, **weighted_stats(terms, w, spot, sa)) for lb, w in cfg.weight_sets]
    out["vsens"] = [dict(f=f, **weighted_stats(parts, weights, spot, sa)) for f, parts in vsens_terms.items()]
    out["risk_neutral"] = risk_neutral(spot, cfg.rf, cfg.iv30, cfg.horizon_years, 300_000, rng, sa)
    out["risk_neutral_evt"] = risk_neutral(spot, cfg.rf, cfg.event_adjusted_iv(), cfg.horizon_years, 300_000, rng, sa)

    (outdir / "results.json").write_text(json.dumps(out, indent=1))
    b = out["blend"]
    print(f"\nBLEND median={b['p50']:.2f} ({b['med_ret']:+.1%}) p5={b['p5']:.2f} p95={b['p95']:.2f} "
          f"P(gain)={b['P_gain']:.1%} logvol={b['realised_vol']:.3f}")
    print(f"wrote {outdir/'results.json'}")

    # ------------------------------------------------------------ aux data + report
    if not args.no_aux:
        from . import aux
        aux.write_tails(cfg, outdir, seed=args.seed)
        aux.write_path(cfg, outdir)
        sys.argv = ["build", str(outdir)]
        runpy.run_module("rblxmc.report.build", run_name="__main__")
        print(f"wrote {outdir/'rblx-monte-carlo.html'}")
    D.finalize(ctx)


if __name__ == "__main__":
    main()
