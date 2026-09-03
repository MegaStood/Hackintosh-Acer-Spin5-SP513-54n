"""The simulation kernel (v4).  One code path for CPU and CUDA, written in PyTorch.

Model (per scenario), in log enterprise value L::

    L_{t+1} = L_t + (mu - sigma^2/2) dt + r_t + g_t + j_t

    r_t   = sqrt(h_t) * eps_t                      diffusion, eps ~ standardised Student-t(nu)
    h_t   = omega + (alpha + gamma*1[r_{t-1}<0]) r_{t-1}^2 + beta h_{t-1}     GJR-GARCH(1,1)
            omega = sigma^2 dt (1 - alpha - beta - gamma/2)  so E[h] = sigma^2 dt;
            alpha = beta = 0 gives the constant-volatility model.
    g_t   = scheduled earnings gap on print days: m_i + earn_sd * xi,  xi ~ t(gap_nu) or N(0,1)
            m_i = repricing[i] * ln(anchor/EV0)     (regime resolution at the prints)
    j_t   = compound-Poisson headline jump: N(jm k, js^2 k), k ~ Poisson(lam dt)
          + three explicit factors (v4):
    macro     -D * dx_t,  dx = -kappa x dt + sigma_r sqrt(dt) xi,  corr(xi, eps) = rho
              the 10y-yield deviation from its expected path, priced through a multiple
              duration D; zero mean, so it widens the distribution without moving anchors
    bookings  a per-path anchor deviation delta ~ N(0, fund_sd) delivered through the
              same repricing schedule as the anchor itself (the prints are bookings surprises)
    litigation one resolution event at tau ~ Exp(lam_lit * lit_scale): EV sentiment jump
              ln(1 - s), s ~ N(sent_mean, sent_sd)+, and a cash outflow ~ lognormal(loss)
              from that day on (capped at available net cash).  NOT compensated in the
              drift: anchors are pre-litigation.

Price per share = (EV0 exp(L) + net_cash_t - litigation_cash_t) / shares_t  (cash ramps
linearly to the scenario's terminal value; shares grow linearly by ``dilution``).

Path statistics use a Brownian-bridge correction on the diffusion leg with that day's
conditional variance h_t: touch probability 1 - prod(1 - p_cross), interval minimum from
the bridge-minimum law.  Gaps and jumps are overnight moves checked at their endpoints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch

from .config import Derived, ModelConfig, Scenario


@dataclass
class SimOutput:
    """Everything a rank hands back.  All tensors on the CPU."""

    term: torch.Tensor                   # (n,)  price at the horizon
    rmin: torch.Tensor                   # (n,)  path minimum incl. bridge-sampled intraday lows
    p3: torch.Tensor                     # (n,)  price at ~3 months (day 63)
    p6: torch.Tensor                     # (n,)  price at ~6 months (day 126)
    bands: torch.Tensor                  # (k, G) price on the band grid for the first k paths
    touch: dict[float, torch.Tensor]     # barrier -> (n,) per-path touch probability
    n: int


# ----------------------------------------------------------------------------- randoms
def student_t(shape, nu: int, gen, device, dtype) -> torch.Tensor:
    """Unit-variance Student-t(nu) built from normals so a torch.Generator controls it."""
    z = torch.randn(shape, generator=gen, device=device, dtype=dtype)
    v = torch.zeros(shape, device=device, dtype=dtype)
    for _ in range(int(nu)):
        v += torch.randn(shape, generator=gen, device=device, dtype=dtype) ** 2
    return z / torch.sqrt(v / nu) * math.sqrt((nu - 2) / nu)


def _std_shock(shape, nu: int, gen, device, dtype) -> torch.Tensor:
    """Standardised shock: Student-t(nu) if nu >= 3 else standard normal."""
    if nu and nu >= 3:
        return student_t(shape, nu, gen, device, dtype)
    return torch.randn(shape, generator=gen, device=device, dtype=dtype)


def chunk_seed(seed: int, rank: int, chunk_idx: int, stream: int) -> int:
    return (seed * 1_000_003 + stream * 7_919 + rank * 104_729 + chunk_idx) % (2 ** 63 - 1)


# ----------------------------------------------------------------------------- shock params
@dataclass(frozen=True)
class ShockParams:
    """Everything the kernel needs besides the anchor: a Scenario minus the fundamentals."""

    sigma: float
    nu: int
    earn_sd: float
    gap_nu: int
    lam: float
    jm: float
    js: float
    alpha: float = 0.0
    beta: float = 0.0
    gamma: float = 0.0
    # macro: 10y-yield deviation, OU, priced through a multiple duration
    rate_duration: float = 0.0
    rate_kappa: float = 0.0
    rate_vol: float = 0.0
    rate_rho: float = 0.0
    # bookings/fundamental forecast dispersion (log, delivered via the repricing schedule)
    fund_sd: float = 0.0
    # litigation event
    lit_lam: float = 0.0
    lit_loss_mean: float = 0.0      # $bn cash outflow, lognormal mean
    lit_loss_sdlog: float = 0.6
    lit_sent_mean: float = 0.0      # fractional EV sentiment hit at resolution
    lit_sent_sd: float = 0.0

    @staticmethod
    def from_scenario(cfg: ModelConfig, sc: Scenario) -> "ShockParams":
        return ShockParams(sc.sigma, cfg.nu, sc.earn_sd, cfg.gap_nu, sc.lam, sc.jm, sc.js,
                           cfg.garch_alpha, cfg.garch_beta, cfg.garch_gamma,
                           cfg.rate_duration, cfg.rate_kappa, cfg.rate_vol, cfg.rate_rho,
                           cfg.fund_sd * sc.fund_scale,
                           cfg.lit_lam * sc.lit_scale, cfg.lit_loss_mean * sc.lit_scale,
                           cfg.lit_loss_sdlog, cfg.lit_sent_mean * sc.lit_scale, cfg.lit_sent_sd)


@torch.no_grad()
def log_paths(
    p: ShockParams,
    n: int,
    n_steps: int,
    dt: float,
    *,
    drift: float,
    earn_days: list[int],
    gap_means: list[float],
    gen,
    device,
    dtype,
    h0: torch.Tensor | None = None,
    schedule_sum: float = 0.0,
    gap_fracs: list[float] | None = None,
):
    """Core kernel.  Returns (L, jump, h, cash_adj): cumulative log EV (n, N+1), the
    discrete leg (n, N), the conditional diffusion variance per day (n, N), and the
    litigation cash outflow per day (n, N+1) in $bn (zeros when the factor is off).
    ``schedule_sum`` is the share of repricing delivered at the prints (the rest is
    linear), needed to spread the per-path fundamental deviation the same way."""
    shape = (n, n_steps)
    eps = _std_shock(shape, p.nu, gen, device, dtype)
    vbar = p.sigma * p.sigma * dt
    garch = (p.alpha > 0) or (p.beta > 0) or (p.gamma > 0)
    if garch:
        omega = vbar * (1.0 - p.alpha - p.beta - 0.5 * p.gamma)
        h = torch.empty(shape, device=device, dtype=dtype)
        r = torch.empty(shape, device=device, dtype=dtype)
        ht = torch.full((n,), vbar, device=device, dtype=dtype) if h0 is None else h0.to(device, dtype).clone()
        for t in range(n_steps):
            h[:, t] = ht
            rt = torch.sqrt(ht) * eps[:, t]
            r[:, t] = rt
            ht = omega + (p.alpha + p.gamma * (rt < 0).to(dtype)) * rt * rt + p.beta * ht
        cont = (drift - 0.5 * p.sigma * p.sigma) * dt + r
        del r
    else:
        h = torch.full(shape, vbar, device=device, dtype=dtype)
        cont = (drift - 0.5 * p.sigma * p.sigma) * dt + math.sqrt(vbar) * eps

    # --- macro: OU deviation of the 10y yield, correlated with the equity shock,
    #     priced through a multiple duration (log EV moves by -D * dx each day)
    if p.rate_duration > 0 and p.rate_vol > 0:
        eta = torch.randn(shape, generator=gen, device=device, dtype=dtype)
        xi = p.rate_rho * eps + math.sqrt(max(0.0, 1.0 - p.rate_rho ** 2)) * eta
        del eta
        x = torch.zeros((n,), device=device, dtype=dtype)
        sq = p.rate_vol * math.sqrt(dt)
        for t in range(n_steps):
            dx = -p.rate_kappa * x * dt + sq * xi[:, t]
            cont[:, t] -= p.rate_duration * dx
            x = x + dx
        del xi, x
    del eps

    # --- bookings / fundamental forecast dispersion: a per-path anchor deviation delivered
    #     through the repricing schedule (print means) and the linear remainder
    delta = None
    if p.fund_sd > 0:
        delta = p.fund_sd * torch.randn((n,), generator=gen, device=device, dtype=dtype)
        cont += (delta * (1.0 - schedule_sum) / n_steps)[:, None]

    jump = torch.zeros(shape, device=device, dtype=dtype)
    for i, (d, m) in enumerate(zip(earn_days, gap_means)):
        if 0 <= d < n_steps:
            jump[:, d] += m + p.earn_sd * _std_shock((n,), p.gap_nu, gen, device, dtype)
            if delta is not None and gap_fracs is not None:
                jump[:, d] += delta * gap_fracs[i]
    if p.lam > 0:
        rate = torch.full(shape, p.lam * dt, device=device, dtype=dtype)
        cnt = torch.poisson(rate, generator=gen)
        del rate
        hit = cnt > 0
        if bool(hit.any()):
            jn = torch.randn(shape, generator=gen, device=device, dtype=dtype)
            jump += torch.where(hit, p.jm * cnt + p.js * torch.sqrt(cnt) * jn,
                                torch.zeros((), device=device, dtype=dtype))
            del jn
        del cnt, hit

    # --- litigation: one resolution event per path (if it lands inside the horizon)
    cash_adj = torch.zeros((n, n_steps + 1), device=device, dtype=dtype)
    if p.lit_lam > 0:
        u = torch.rand((n,), generator=gen, device=device, dtype=dtype).clamp_(min=1e-12)
        tau = -torch.log(u) / p.lit_lam                       # years
        k = torch.floor(tau / dt).to(torch.long)              # day index of the event
        inside = k < n_steps
        if bool(inside.any()):
            sent = (p.lit_sent_mean + p.lit_sent_sd * torch.randn((n,), generator=gen, device=device, dtype=dtype)).clamp_(min=0.0, max=0.9)
            if p.lit_loss_mean > 0:
                mu_l = math.log(p.lit_loss_mean) - 0.5 * p.lit_loss_sdlog ** 2
                loss = torch.exp(mu_l + p.lit_loss_sdlog * torch.randn((n,), generator=gen, device=device, dtype=dtype))
                loss = torch.clamp(loss, max=6.0 * p.lit_loss_mean)     # no absurd tails
            else:
                loss = torch.zeros((n,), device=device, dtype=dtype)
            kk = torch.where(inside, k, torch.full_like(k, n_steps - 1))
            hitv = torch.where(inside, torch.log1p(-sent), torch.zeros_like(sent))
            jump.scatter_add_(1, kk[:, None], hitv[:, None])
            days_idx = torch.arange(n_steps + 1, device=device)[None, :]
            after = (days_idx > kk[:, None]) & inside[:, None]
            cash_adj = torch.where(after, loss[:, None], torch.zeros((), device=device, dtype=dtype))
            del sent, loss, kk, hitv, days_idx, after
        del u, tau, k, inside

    L = torch.cat([torch.zeros((n, 1), device=device, dtype=dtype),
                   torch.cumsum(cont + jump, dim=1)], dim=1)
    del cont
    return L, jump, h, cash_adj


# ----------------------------------------------------------------------------- full simulation
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
    n_steps, dt = cfg.n_steps, cfg.dt
    p = ShockParams.from_scenario(cfg, sc)
    grid = torch.tensor(cfg.grid, device=device)

    days = torch.arange(n_steps + 1, device=device, dtype=dtype)
    shares_t = cfg.shares0 * (1.0 + cfg.dilution * days / n_steps)
    cash_t = cfg.net_cash0 + (sc.cash_t - cfg.net_cash0) * days / n_steps
    ev0 = cfg.ev0

    outs = {k: [] for k in ("term", "rmin", "p3", "p6", "bands")}
    touch_out = {b: [] for b in cfg.barriers}
    kept = done = ci = 0
    while done < n_paths:
        n = min(chunk, n_paths - done)
        gen = torch.Generator(device=device).manual_seed(chunk_seed(seed, rank, ci, sc_index))
        L, jump, h, cash_adj = log_paths(
            p, n, n_steps, dt, drift=der.mu, earn_days=list(cfg.earn_days),
            gap_means=list(der.gap_means), gen=gen, device=device, dtype=dtype,
            schedule_sum=sum(cfg.schedule), gap_fracs=list(cfg.schedule))
        # a settlement cannot take more cash than the company has: net cash floors at zero
        cash_p = torch.clamp(cash_t - cash_adj, min=0.0)        # (n, N+1)
        px = (ev0 * torch.exp(L) + cash_p) / shares_t

        outs["term"].append(px[:, -1].cpu())
        outs["p3"].append(px[:, min(63, n_steps)].cpu())
        outs["p6"].append(px[:, min(126, n_steps)].cpu())
        if kept < band_keep:
            take = min(n, band_keep - kept)
            outs["bands"].append(px[:take][:, grid].to(torch.float32).cpu())
            kept += take

        # --- Brownian-bridge statistics on the diffusion leg, using that day's variance
        open_ = L[:, :-1] + jump
        close = L[:, 1:]
        u = torch.rand((n, n_steps), generator=gen, device=device, dtype=dtype).clamp_(min=1e-12)
        m = 0.5 * (open_ + close - torch.sqrt((open_ - close) ** 2 - 2.0 * h * torch.log(u)))
        px_min_intra = (ev0 * torch.exp(m) + cash_p[:, 1:]) / shares_t[1:]
        rmin = torch.minimum(px.min(dim=1).values, px_min_intra.min(dim=1).values)
        outs["rmin"].append(rmin.cpu())
        del u, m, px_min_intra

        for b in cfg.barriers:
            blog = torch.log(torch.clamp(b * shares_t - cash_p, min=1e-9)) - math.log(ev0)
            a0 = open_ - blog[:, 1:]
            a1 = close - blog[:, 1:]
            hit_ep = (a0 <= 0) | (a1 <= 0)
            p_cross = torch.exp(torch.clamp(-2.0 * a0 * a1 / h, max=0.0))
            p_cross = torch.where(hit_ep, torch.ones((), device=device, dtype=dtype), p_cross)
            p_no = torch.prod(1.0 - p_cross, dim=1)
            start_hit = px[:, 0] <= b
            touch_out[b].append(torch.where(start_hit, torch.ones_like(p_no), 1.0 - p_no).cpu())
            del a0, a1, hit_ep, p_cross, p_no, blog

        del L, jump, h, px, open_, close, cash_adj, cash_p
        done += n
        ci += 1

    cat = lambda k: torch.cat(outs[k]) if outs[k] else torch.empty(0)
    return SimOutput(
        term=cat("term"), rmin=cat("rmin"), p3=cat("p3"), p6=cat("p6"),
        bands=torch.cat(outs["bands"]) if outs["bands"] else torch.empty((0, len(cfg.grid))),
        touch={b: torch.cat(v) for b, v in touch_out.items()},
        n=n_paths,
    )


@torch.no_grad()
def horizon_log_returns(
    p: ShockParams,
    n: int,
    horizons: list[int],
    dt: float,
    *,
    earn_offsets: list[int],
    gap_means: list[float] | None = None,
    drift: float = 0.0,
    h0: float | None = None,
    seed: int = 0,
    device="cpu",
    dtype=torch.float32,
) -> dict[int, torch.Tensor]:
    """Log-return distribution at several horizons from one start (used by the backtest).
    ``earn_offsets`` are print days relative to the start; ``h0`` the filtered variance."""
    device = torch.device(device)
    n_steps = max(horizons)
    gen = torch.Generator(device=device).manual_seed(seed)
    h0t = None if h0 is None else torch.full((n,), float(h0), device=device, dtype=dtype)
    L, _, _, _ = log_paths(p, n, n_steps, dt, drift=drift, earn_days=list(earn_offsets),
                        gap_means=list(gap_means or [0.0] * len(earn_offsets)),
                        gen=gen, device=device, dtype=dtype, h0=h0t)
    return {hz: L[:, hz].cpu() for hz in horizons}


def merge(parts: list[SimOutput]) -> SimOutput:
    parts = [q for q in parts if q is not None and q.n > 0]
    return SimOutput(
        term=torch.cat([q.term for q in parts]),
        rmin=torch.cat([q.rmin for q in parts]),
        p3=torch.cat([q.p3 for q in parts]),
        p6=torch.cat([q.p6 for q in parts]),
        bands=torch.cat([q.bands for q in parts]),
        touch={b: torch.cat([q.touch[b] for q in parts]) for b in parts[0].touch},
        n=sum(q.n for q in parts),
    )
