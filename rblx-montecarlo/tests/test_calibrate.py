"""Parameter recovery on synthetic data: simulate a long history from the engine with known
shock parameters, then check that `calibrate.estimate` recovers them and that `backtest`
reports nominal coverage.  Tolerances are loose: 8 synthetic years is a small sample."""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
import torch

from rblxmc.backtest import run_backtest
from rblxmc.calibrate import estimate
from rblxmc.config import ModelConfig
from rblxmc.engine import ShockParams, log_paths

CFG = Path(__file__).resolve().parents[1] / "configs" / "rblx_2026-09.toml"
TRUE = ShockParams(sigma=0.45, nu=5, earn_sd=0.16, gap_nu=5, lam=1.2, jm=-0.05, js=0.06,
                   alpha=0.07, beta=0.88, gamma=0.03)          # equity-space truth


def business_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def synthetic():
    years = 20
    n_steps = 252 * years
    earn = [30 + 63 * q for q in range(4 * years)]
    gen = torch.Generator().manual_seed(11)
    L, _, _, _ = log_paths(TRUE, 1, n_steps, 1 / 252, drift=0.0, earn_days=earn,
                           gap_means=[0.0] * len(earn), gen=gen, device="cpu", dtype=torch.float64)
    px = 50.0 * np.exp(L[0].numpy())
    dates = business_days(date(2018, 1, 2), n_steps + 1)
    return dates, px, earn


def test_parameter_recovery(synthetic):
    dates, px, earn = synthetic
    est = estimate(dates, px, earn_idx=earn, garch_steps=250)
    true_total = (TRUE.sigma ** 2 + TRUE.lam * (TRUE.jm ** 2 + TRUE.js ** 2)) ** 0.5
    assert abs(est.total_vol_eq / true_total - 1) < 0.10, (est.total_vol_eq, true_total)
    assert abs(est.sigma_eq_all / TRUE.sigma - 1) < 0.12, est.sigma_eq_all
    assert abs(est.earn_sd_eq / TRUE.earn_sd - 1) < 0.25, est.earn_sd_eq
    assert 0.3 * TRUE.lam < est.lam < 3.0 * TRUE.lam, est.lam          # weakly identified
    assert 3 <= est.nu <= 8, est.nu
    pers = est.garch["alpha"] + est.garch["beta"] + 0.5 * est.garch["gamma"]
    assert abs(pers - (TRUE.alpha + TRUE.beta + 0.5 * TRUE.gamma)) < 0.12, est.garch


def test_backtest_coverage_on_synthetic(synthetic):
    dates, px, earn = synthetic
    cfg = ModelConfig.load(CFG)
    lev = cfg.lev_spot
    base = next(s for s in cfg.scenarios if s.key == "base")
    base_t = replace(base, sigma=TRUE.sigma / lev, earn_sd=TRUE.earn_sd / lev, lam=TRUE.lam,
                     jm=TRUE.jm / lev, js=TRUE.js / lev)
    cfg_t = replace(cfg, nu=TRUE.nu, gap_nu=TRUE.gap_nu, garch_alpha=TRUE.alpha,
                    garch_beta=TRUE.beta, garch_gamma=TRUE.gamma,
                    scenarios=[base_t if s.key == "base" else s for s in cfg.scenarios])
    res = run_backtest(cfg_t, dates, px, horizons=(5, 21, 63), step=21, paths=3000, earn_idx=earn)
    for h in res["horizons"]:
        # windows overlap at 63d, so the effective sample is smaller than n; keep the bar honest
        assert abs(h["cover90"] - 0.90) < 0.07, h
        assert abs(h["mean_pit"] - 0.5) < 0.06, h
        assert h["ks_p"] > 0.005, h
