"""Fit the within-scenario shock parameters from price history.

    python -m rblxmc.calibrate --csv data/RBLX.csv --config configs/rblx_2026-09.toml \
        --out configs/rblx_calibrated.toml [--yields data/DGS10.csv] [--earnings 2026-07-31 ...]

or, where the network is open,  --ticker RBLX --start 2022-01-01  (uses yfinance).

What is estimated and how
-------------------------
earnings days     given explicitly, else the largest |return| day inside each quarter's usual
                  reporting window (Jan25-Feb25, Apr20-May20, Jul20-Aug20, Oct20-Nov20)
earn_sd, gap_nu   sd of print-day log moves (converted to EV space by / lev_spot) and a
                  Student-t dof by grid MLE on the standardised moves (small sample: flagged)
lam, jm, js       non-print days with |r| > 4 x robust sigma are headline jumps: intensity
                  per year, mean and sd of their log size
sigma             annualised sd of the remaining (non-print, non-jump) returns, overall and
                  split into a drawdown regime (price < 75% of trailing 1y max) and the rest
nu                Student-t dof by grid MLE on the standardised diffusion returns
GJR-GARCH(1,1)    quasi-MLE on the diffusion returns, fitted with torch (Adam) under
                  stationarity constraints; reports alpha, beta, gamma
rate_duration     if a yield series is given: OLS of daily returns on daily yield changes
rate_vol, kappa   sd of yield changes (annualised) and AR(1) mean reversion of the level

Anchors, weights, dilution, litigation and the repricing schedule are judgments and are
copied through unchanged.  The output TOML is a full config: run it exactly like the
judgment one and compare.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from .config import ModelConfig

REPORT_WINDOWS = [((1, 25), (2, 25)), ((4, 20), (5, 20)), ((7, 20), (8, 20)), ((10, 20), (11, 20))]


# ------------------------------------------------------------------------- data loading
def load_prices_csv(path: str | Path) -> tuple[list[date], np.ndarray]:
    """Date + close from a Yahoo/stooq-style CSV (uses 'Adj Close' if present)."""
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: empty")
    cols = {c.lower().strip(): c for c in rows[0].keys()}
    dcol = cols.get("date") or next(iter(rows[0].keys()))
    pcol = cols.get("adj close") or cols.get("adj_close") or cols.get("close") or cols.get("zamkniecie")
    if pcol is None:
        raise ValueError(f"{path}: no Close column in {list(rows[0].keys())}")
    out = []
    for r in rows:
        try:
            d = date.fromisoformat(r[dcol][:10])
            p = float(r[pcol])
        except (ValueError, TypeError):
            continue
        if p > 0:
            out.append((d, p))
    out.sort()
    return [d for d, _ in out], np.array([p for _, p in out], dtype=np.float64)


def load_prices_yf(ticker: str, start: str) -> tuple[list[date], np.ndarray]:
    import yfinance as yf  # optional dependency
    h = yf.Ticker(ticker).history(start=start, interval="1d", auto_adjust=True)
    return [d.date() for d in h.index], h["Close"].to_numpy(dtype=np.float64)


def load_yields_csv(path: str | Path) -> dict[date, float]:
    """FRED DGS10-style CSV (DATE, DGS10 in percent) or any Date,Value file -> yield in decimals."""
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            vals = list(r.values())
            try:
                d = date.fromisoformat(vals[0][:10])
                y = float(vals[1])
            except (ValueError, TypeError, IndexError):
                continue
            out[d] = y / 100.0 if y > 1.0 else y
    return out


# ------------------------------------------------------------------------- helpers
def detect_earnings_days(dates: list[date], r: np.ndarray) -> list[int]:
    """Index (into r) of the largest |return| day in each quarter's reporting window."""
    idx = []
    years = sorted({d.year for d in dates})
    for y in years:
        for (m1, d1), (m2, d2) in REPORT_WINDOWS:
            lo, hi = date(y, m1, d1), date(y, m2, d2)
            cand = [i for i, d in enumerate(dates[1:]) if lo <= d <= hi]
            if cand:
                idx.append(max(cand, key=lambda i: abs(r[i])))
    return sorted(set(idx))


def t_mle_nu(z: np.ndarray, grid=range(3, 31)) -> int:
    """Grid MLE for the dof of a unit-variance Student-t on standardised data."""
    best, best_ll = 3, -np.inf
    for nu in grid:
        s = math.sqrt((nu - 2) / nu)
        x = z / s
        ll = (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi) - math.log(s)) * len(x) \
             - (nu + 1) / 2 * np.sum(np.log1p(x * x / nu))
        if ll > best_ll:
            best, best_ll = nu, ll
    return int(best)


def _nelder_mead(f, x0, step=0.5, iters=400, tol=1e-7):
    n = len(x0)
    pts = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]; vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) < tol:
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1]); fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1]); fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c); fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def fit_gjr_garch(r: np.ndarray, steps: int = 400, lr: float = 0.0) -> dict:
    """Gaussian QMLE for r_t = sqrt(h_t) z_t, h_t = w + (a + g 1[r<0]) r^2 + b h, by
    Nelder-Mead over transformed parameters so a + b + g/2 < 1 always holds."""
    x = np.asarray(r, float) - float(np.mean(r))
    v = float(x.var())
    neg = (x < 0).astype(float)
    x2 = x * x

    def unpack(u):
        pers = 0.9995 / (1 + math.exp(-u[0]))
        e = np.exp(u[1:] - np.max(u[1:])); w3 = e / e.sum()
        return pers * w3[0], pers * w3[1], 2 * pers * w3[2], pers

    def nll(u):
        a, b, g, pers = unpack(u)
        omega = v * (1 - pers)
        h = v; ll = 0.0
        for t in range(len(x)):
            ll += math.log(h) + x2[t] / h
            h = omega + (a + g * neg[t]) * x2[t] + b * h
        return 0.5 * ll

    u0 = np.array([2.0, math.log(0.07), math.log(0.88), math.log(0.05)])
    u, val = _nelder_mead(nll, u0, iters=steps)
    a, b, g, pers = unpack(u)
    return dict(alpha=float(a), beta=float(b), gamma=float(g), persistence=float(pers), nll=float(val))


def garch_filter(r: np.ndarray, alpha: float, beta: float, gamma: float, vbar: float) -> np.ndarray:
    """Conditional variance path h_t (aligned with r) for the fitted model."""
    omega = vbar * (1 - alpha - beta - 0.5 * gamma)
    h = np.empty(len(r))
    ht = vbar
    for t in range(len(r)):
        h[t] = ht
        ht = omega + (alpha + gamma * (r[t] < 0)) * r[t] * r[t] + beta * ht
    return h


# ------------------------------------------------------------------------- estimation
@dataclass
class Estimates:
    n_days: int
    years: float
    earn_idx: list[int]
    print_moves: list[float]
    earn_sd_eq: float
    gap_nu: int
    lam: float
    jm: float
    js: float
    sigma_eq_all: float
    sigma_eq_drawdown: float
    sigma_eq_normal: float
    total_vol_eq: float          # annualised sd of ALL non-print returns (jumps included)
    nu: int
    garch: dict
    rate: dict | None
    notes: list[str]


def estimate(dates: list[date], px: np.ndarray, *, earn_idx: list[int] | None = None,
             yields: dict[date, float] | None = None, fit_garch: bool = True,
             garch_steps: int = 400, jump_k: float = 5.0) -> Estimates:
    r = np.diff(np.log(px))
    n = len(r)
    years = (dates[-1] - dates[0]).days / 365.25
    notes = []
    if earn_idx is None:
        earn_idx = detect_earnings_days(dates, r)
        notes.append(f"earnings days auto-detected ({len(earn_idx)}); pass --earnings to override")
    is_print = np.zeros(n, bool)
    is_print[[i for i in earn_idx if 0 <= i < n]] = True
    pm = r[is_print]
    earn_sd = float(pm.std(ddof=1)) if len(pm) > 1 else float("nan")
    gap_nu = t_mle_nu(pm / earn_sd, range(3, 16)) if len(pm) >= 6 else 0
    if len(pm) < 8:
        notes.append(f"only {len(pm)} print moves: gap_nu and earn_sd are rough")

    np_ = r[~is_print]
    mad_sig = 1.4826 * np.median(np.abs(np_ - np.median(np_)))
    jump_mask = np.abs(np_) > jump_k * mad_sig
    jumps = np_[jump_mask]
    lam = float(len(jumps) / years)
    jm = float(jumps.mean()) if len(jumps) else 0.0
    js = float(jumps.std(ddof=1)) if len(jumps) > 1 else 0.0
    diff = np_[~jump_mask]
    # the split between diffusion and jumps is only weakly identified; the TOTAL non-print
    # variance is not, so size the diffusion as total minus the jump contribution
    total_var = float(np.mean((np_ - np_.mean()) ** 2) * 252)
    total_vol = math.sqrt(total_var)
    jump_var = lam * (jm * jm + js * js)
    sigma_all = math.sqrt(max(total_var - jump_var, 0.25 * total_var))
    scale_reg = sigma_all / (float(diff.std(ddof=1)) * math.sqrt(252))   # regime sds on the same footing

    # regime split: drawdown = price below 75% of trailing 1y max
    roll_max = np.array([px[max(0, i - 252):i + 1].max() for i in range(len(px))])
    dd = (px / roll_max)[1:] < 0.75
    dd_np = dd[~is_print][~jump_mask]
    sig_dd = float(diff[dd_np].std(ddof=1) * math.sqrt(252) * scale_reg) if dd_np.sum() > 40 else float("nan")
    sig_nm = float(diff[~dd_np].std(ddof=1) * math.sqrt(252) * scale_reg) if (~dd_np).sum() > 40 else float("nan")
    if not (dd_np.sum() > 40):
        notes.append("fewer than 40 drawdown-regime days: bear sigma falls back to the overall value")

    garch = fit_gjr_garch(diff, steps=garch_steps) if fit_garch else {}
    if garch:
        # tail index on GARCH-standardised residuals, so clustering is not read as fat tails
        hh = garch_filter(diff - diff.mean(), garch["alpha"], garch["beta"], garch["gamma"], float(diff.var()))
        z = (diff - diff.mean()) / np.sqrt(hh)
    else:
        z = (diff - diff.mean()) / diff.std(ddof=1)
    nu = t_mle_nu(z / z.std(ddof=1))

    rate = None
    if yields:
        ys = np.array([yields.get(d, np.nan) for d in dates])
        ok = ~np.isnan(ys)
        if ok.sum() > 100:
            # align: daily return r[i] spans dates[i]->dates[i+1]; yield change over the same span
            dy = np.diff(ys)
            m = ~np.isnan(dy) & ~is_print
            beta = float(np.polyfit(dy[m], r[m], 1)[0])            # return per 1.00 of yield
            lvl = ys[ok]
            dev = lvl - lvl.mean()
            phi = float(np.polyfit(dev[:-1], dev[1:], 1)[0])
            rate = dict(duration=max(0.0, -beta), vol=float(np.nanstd(dy) * math.sqrt(252)),
                        kappa=max(0.0, -252 * math.log(max(min(phi, 0.9999), 1e-6))),
                        rho=float(np.corrcoef(dy[m], r[m])[0, 1]))
    return Estimates(n, years, earn_idx, pm.tolist(), earn_sd, gap_nu, lam, jm, js,
                     sigma_all, sig_dd, sig_nm, total_vol, nu, garch, rate, notes)


# ------------------------------------------------------------------------- write config
def write_calibrated_toml(base_cfg_path: str | Path, est: Estimates, cfg: ModelConfig, out: str | Path) -> None:
    """Copy the judgment TOML and substitute the fitted shock parameters (EV space)."""
    lev = cfg.lev_spot
    text = Path(base_cfg_path).read_text()

    def set_model(key, val):
        nonlocal text
        pat = re.compile(rf"^(\s*{key}\s*=\s*)([^\n#]*)", re.M)
        if pat.search(text):
            text = pat.sub(lambda m: f"{m.group(1)}{val}", text, count=1)
        else:
            text = text.replace("[model]\n", f"[model]\n{key} = {val}\n", 1)

    set_model("nu", est.nu)
    set_model("gap_nu", est.gap_nu)
    if est.garch:
        set_model("garch_alpha", round(est.garch["alpha"], 4))
        set_model("garch_beta", round(est.garch["beta"], 4))
        set_model("garch_gamma", round(est.garch["gamma"], 4))
    if est.rate:
        for k, v in (("rate_duration", est.rate["duration"]), ("rate_vol", est.rate["vol"]),
                     ("rate_kappa", est.rate["kappa"]), ("rate_rho", est.rate["rho"])):
            text = re.sub(rf"^(\s*{k}\s*=\s*)([^\n#]*)", lambda m, v=v: f"{m.group(1)}{round(v, 4)}", text, flags=re.M)

    sig_all = est.sigma_eq_all / lev
    sig_bear = (est.sigma_eq_drawdown if est.sigma_eq_drawdown == est.sigma_eq_drawdown else est.sigma_eq_all) / lev
    sig_bull = (est.sigma_eq_normal if est.sigma_eq_normal == est.sigma_eq_normal else est.sigma_eq_all) / lev
    earn_sd = est.earn_sd_eq / lev
    per_key = {"bear": sig_bear, "base": sig_all, "bull": sig_bull}
    blocks = text.split("[[scenario]]")
    for i in range(1, len(blocks)):
        b = blocks[i]
        key = re.search(r'key\s*=\s*"(\w+)"', b).group(1)
        sig = per_key.get(key, sig_all)
        b = re.sub(r"^(\s*sigma\s*=\s*)([^\n#]*)", lambda m: f"{m.group(1)}{sig:.4f}", b, flags=re.M)
        b = re.sub(r"^(\s*earn_sd\s*=\s*)([^\n#]*)", lambda m: f"{m.group(1)}{earn_sd:.4f}", b, flags=re.M)
        b = re.sub(r"^(\s*lam\s*=\s*)([^\n#]*)", lambda m: f"{m.group(1)}{est.lam:.3f}", b, flags=re.M)
        b = re.sub(r"^(\s*jm\s*=\s*)([^\n#]*)", lambda m: f"{m.group(1)}{est.jm / lev:.4f}", b, flags=re.M)
        b = re.sub(r"^(\s*js\s*=\s*)([^\n#]*)", lambda m: f"{m.group(1)}{est.js / lev:.4f}", b, flags=re.M)
        blocks[i] = b
    text = "[[scenario]]".join(blocks)
    header = ("# DATA-CALIBRATED shock parameters (see rblxmc.calibrate); anchors, weights,\n"
              "# dilution, litigation and repricing schedule copied from the judgment config.\n")
    Path(out).write_text(header + text)


def summary_table(est: Estimates, cfg: ModelConfig) -> str:
    lev = cfg.lev_spot
    base = next(s for s in cfg.scenarios if s.key == "base")
    rows = [
        ("diffusion sigma (EV)", f"{base.sigma:.3f}", f"{est.sigma_eq_all / lev:.3f}"),
        ("  drawdown regime", "-", f"{est.sigma_eq_drawdown / lev:.3f}"),
        ("  normal regime", "-", f"{est.sigma_eq_normal / lev:.3f}"),
        ("earnings gap sd (EV)", f"{base.earn_sd:.3f}", f"{est.earn_sd_eq / lev:.3f}"),
        ("gap Student-t dof", f"{cfg.gap_nu}", f"{est.gap_nu}"),
        ("headline jumps / yr", f"{base.lam:.2f}", f"{est.lam:.2f}"),
        ("  mean, sd (EV)", f"{base.jm:+.3f}, {base.js:.3f}", f"{est.jm / lev:+.3f}, {est.js / lev:.3f}"),
        ("total non-print vol (equity)", "-", f"{est.total_vol_eq:.3f}"),
        ("diffusion dof nu", f"{cfg.nu}", f"{est.nu}"),
    ]
    if est.garch:
        rows.append(("GJR-GARCH a / b / g", f"{cfg.garch_alpha:.3f} / {cfg.garch_beta:.3f} / {cfg.garch_gamma:.3f}",
                     f"{est.garch['alpha']:.3f} / {est.garch['beta']:.3f} / {est.garch['gamma']:.3f}"))
    if est.rate:
        rows.append(("rate duration / vol / kappa / rho",
                     f"{cfg.rate_duration:.1f} / {cfg.rate_vol:.4f} / {cfg.rate_kappa:.2f} / {cfg.rate_rho:+.2f}",
                     f"{est.rate['duration']:.1f} / {est.rate['vol']:.4f} / {est.rate['kappa']:.2f} / {est.rate['rho']:+.2f}"))
    w = max(len(r[0]) for r in rows)
    out = [f"{'parameter':<{w}}  {'judgment':>16}  {'fitted':>16}", "-" * (w + 36)]
    out += [f"{a:<{w}}  {b:>16}  {c:>16}" for a, b, c in rows]
    out.append(f"\n{est.n_days} daily returns over {est.years:.1f} years; {len(est.print_moves)} print moves: "
               + ", ".join(f"{m*100:+.1f}%" for m in est.print_moves[-8:]))
    out += [f"note: {n}" for n in est.notes]
    return "\n".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Calibrate rblxmc shock parameters from price history")
    ap.add_argument("--config", required=True, help="judgment TOML to copy anchors/weights from")
    ap.add_argument("--csv", help="Date,Close CSV (Yahoo/stooq format)")
    ap.add_argument("--ticker", help="download with yfinance instead of --csv")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--yields", help="optional 10y yield CSV (FRED DGS10) for the macro factor")
    ap.add_argument("--earnings", nargs="*", help="explicit earnings dates YYYY-MM-DD")
    ap.add_argument("--out", default="configs/rblx_calibrated.toml")
    ap.add_argument("--no-garch", action="store_true")
    ap.add_argument("--garch-steps", type=int, default=400)
    args = ap.parse_args(argv)

    cfg = ModelConfig.load(args.config)
    if args.csv:
        dates, px = load_prices_csv(args.csv)
    elif args.ticker:
        dates, px = load_prices_yf(args.ticker, args.start)
    else:
        ap.error("give --csv or --ticker")
    earn_idx = None
    if args.earnings:
        want = {date.fromisoformat(s) for s in args.earnings}
        earn_idx = [i for i, d in enumerate(dates[1:]) if d in want]
    yields = load_yields_csv(args.yields) if args.yields else None
    est = estimate(dates, px, earn_idx=earn_idx, yields=yields,
                   fit_garch=not args.no_garch, garch_steps=args.garch_steps)
    print(summary_table(est, cfg))
    write_calibrated_toml(args.config, est, cfg, args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
