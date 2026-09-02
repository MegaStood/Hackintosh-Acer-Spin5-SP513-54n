"""The simulation kernel.  One code path for CPU and CUDA, written in PyTorch.

Model (per scenario)
--------------------
Total enterprise value follows a jump-diffusion in log space::

    dL = (mu - sigma^2/2) dt + sigma sqrt(dt) eps          eps ~ standardised Student-t(nu)
       + sum over scheduled earnings gaps  N(m_d, earn_sd^2)   (m_d = q3_mean on the first print, else 0)
       + compound-Poisson headline jumps   N(jm k, js^2 k),  k ~ Poisson(lam dt)

Price per share = (EV0 * exp(L) + net_cash_t) / shares_t, with cash ramping linearly to
the scenario's terminal value and shares growing linearly by ``dilution``.

Path statistics use a Brownian-bridge correction on the diffusion leg: the touch
probability of a barrier is 1 - prod(1 - p_cross) with the standard crossing law, and the
interval minimum is sampled from the bridge-minimum law.  Both treat the day's gap as an
overnight move (checked at its endpoints) and the diffusion as intraday.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from .config import Derived, ModelConfig, Scenario


@dataclass
class SimOutput:
    """Everything a rank needs to hand back.  All tensors live on the CPU."""

    term: torch.Tensor                   # (n,)  price at the horizon
    rmin: torch.Tensor                   # (n,)  path minimum incl. bridge-sampled intraday lows
    p3: torch.Tensor                     # (n,)  price at ~3 months (day 63)
    p6: torch.Tensor                     # (n,)  price at ~6 months (day 126)
    bands: torch.Tensor                  # (k, G) price on the band grid for the first k paths
    touch: dict[float, torch.Tensor]     # barrier -> (n,) per-path touch probability
    n: int


def _student_t(shape, nu: int, gen, device, dtype) -> torch.Tensor:
    """Unit-variance Student-t(nu) draws built from normals so a torch.Generator controls them.

    t = Z / sqrt(V/nu) with V ~ chi^2(nu) = sum of nu squared normals; scaled by
    sqrt((nu-2)/nu) so the variance is exactly 1.
    """
    z = torch.randn(shape, generator=gen, device=device, dtype=dtype)
    v = torch.zeros(shape, device=device, dtype=dtype)
    for _ in range(nu):
        v += torch.randn(shape, generator=gen, device=device, dtype=dtype) ** 2
    return z / torch.sqrt(v / nu) * math.sqrt((nu - 2) / nu)


def _chunk_seed(seed: int, rank: int, chunk_idx: int, sc_index: int) -> int:
    # deterministic, well-separated streams per (scenario, rank, chunk)
    return (seed * 1_000_003 + sc_index * 7_919 + rank * 104_729 + chunk_idx) % (2 ** 63 - 1)


@torch.no_grad()
def simulate(
    cfg: ModelConfig,
    sc: Scenario,
    der: Derived,
    n_paths: int,
    *,
    chunk: int = 250_000,
    device: str | torch.device = "cpu",
    dtype: torch.dtype = torch.float32,
    seed: int = 20260901,
    rank: int = 0,
    sc_index: int = 0,
    band_keep: int = 100_000,
) -> SimOutput:
    device = torch.device(device)
    n_steps = cfg.n_steps
    dt = cfg.dt
    sqdt = math.sqrt(dt)
    sig = sc.sigma
    sig2dt = sig * sig * dt
    grid = torch.tensor(cfg.grid, device=device)

    days = torch.arange(n_steps + 1, device=device, dtype=dtype)
    shares_t = cfg.shares0 * (1.0 + cfg.dilution * days / n_steps)             # (N+1,)
    cash_t = cfg.net_cash0 + (sc.cash_t - cfg.net_cash0) * days / n_steps       # (N+1,)
    ev0 = cfg.ev0
    # log-EV barrier per day: the EV level at which price == barrier
    bar_log = {
        b: torch.log(torch.clamp(b * shares_t - cash_t, min=1e-9)) - math.log(ev0)
        for b in cfg.barriers
    }
    earn_days = list(cfg.earn_days)

    outs = {k: [] for k in ("term", "rmin", "p3", "p6", "bands")}
    touch_out = {b: [] for b in cfg.barriers}
    kept = 0
    ci = 0
    done = 0
    while done < n_paths:
        n = min(chunk, n_paths - done)
        gen = torch.Generator(device=device).manual_seed(_chunk_seed(seed, rank, ci, sc_index))
        shape = (n, n_steps)

        # --- continuous leg: drift + Student-t diffusion
        eps = _student_t(shape, cfg.nu, gen, device, dtype)
        cont = (der.mu - 0.5 * sig * sig) * dt + sig * sqdt * eps
        del eps

        # --- discrete leg: scheduled gaps + compound-Poisson headline jumps
        jump = torch.zeros(shape, device=device, dtype=dtype)
        for i, d in enumerate(earn_days):
            mean = der.q3_mean if i == 0 else 0.0
            jump[:, d] += mean + sc.earn_sd * torch.randn(n, generator=gen, device=device, dtype=dtype)
        rate = torch.full(shape, sc.lam * dt, device=device, dtype=dtype)
        cnt = torch.poisson(rate, generator=gen)
        del rate
        hit = cnt > 0
        if bool(hit.any()):
            jn = torch.randn(shape, generator=gen, device=device, dtype=dtype)
            jump += torch.where(hit, sc.jm * cnt + sc.js * torch.sqrt(cnt) * jn, torch.zeros((), device=device, dtype=dtype))
            del jn
        del cnt, hit

        # --- log path and price
        L = torch.cat([torch.zeros((n, 1), device=device, dtype=dtype),
                       torch.cumsum(cont + jump, dim=1)], dim=1)
        px = (ev0 * torch.exp(L) + cash_t) / shares_t                            # (n, N+1)

        outs["term"].append(px[:, -1].cpu())
        outs["p3"].append(px[:, 63].cpu())
        outs["p6"].append(px[:, 126].cpu())
        if kept < band_keep:
            take = min(n, band_keep - kept)
            outs["bands"].append(px[:take][:, grid].to(torch.float32).cpu())
            kept += take

        # --- Brownian-bridge statistics on the diffusion leg
        open_ = L[:, :-1] + jump          # log EV after the overnight gap
        close = L[:, 1:]
        u = torch.rand(shape, generator=gen, device=device, dtype=dtype).clamp_(min=1e-12)
        disc = (open_ - close) ** 2 - 2.0 * sig2dt * torch.log(u)
        m = 0.5 * (open_ + close - torch.sqrt(disc))                             # interval min of log EV
        px_min_intra = (ev0 * torch.exp(m) + cash_t[1:]) / shares_t[1:]
        rmin = torch.minimum(px.min(dim=1).values, px_min_intra.min(dim=1).values)
        outs["rmin"].append(rmin.cpu())
        del u, disc, m, px_min_intra

        for b, blog in bar_log.items():
            a0 = open_ - blog[1:]
            a1 = close - blog[1:]
            hit_ep = (a0 <= 0) | (a1 <= 0)
            p_cross = torch.exp(torch.clamp(-2.0 * a0 * a1 / sig2dt, max=0.0))
            p_cross = torch.where(hit_ep, torch.ones((), device=device, dtype=dtype), p_cross)
            p_no = torch.prod(1.0 - p_cross, dim=1)
            start_hit = px[:, 0] <= b
            touch_out[b].append(torch.where(start_hit, torch.ones_like(p_no), 1.0 - p_no).cpu())
            del a0, a1, hit_ep, p_cross, p_no

        del cont, jump, L, px, open_, close
        done += n
        ci += 1

    cat = lambda k: torch.cat(outs[k]) if outs[k] else torch.empty(0)
    return SimOutput(
        term=cat("term"), rmin=cat("rmin"), p3=cat("p3"), p6=cat("p6"),
        bands=torch.cat(outs["bands"]) if outs["bands"] else torch.empty((0, len(cfg.grid))),
        touch={b: torch.cat(v) for b, v in touch_out.items()},
        n=n_paths,
    )


def merge(parts: list[SimOutput]) -> SimOutput:
    """Concatenate per-rank outputs into one (used on the main rank after a gather)."""
    parts = [p for p in parts if p is not None and p.n > 0]
    return SimOutput(
        term=torch.cat([p.term for p in parts]),
        rmin=torch.cat([p.rmin for p in parts]),
        p3=torch.cat([p.p3 for p in parts]),
        p6=torch.cat([p.p6 for p in parts]),
        bands=torch.cat([p.bands for p in parts]),
        touch={b: torch.cat([p.touch[b] for p in parts]) for b in parts[0].touch},
        n=sum(p.n for p in parts),
    )
