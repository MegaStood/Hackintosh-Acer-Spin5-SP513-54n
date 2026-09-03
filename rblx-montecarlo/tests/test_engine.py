"""Sanity tests on the CPU backend (small path counts; loose but meaningful tolerances)."""
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch

from rblxmc.config import ModelConfig
from rblxmc.engine import ShockParams, horizon_log_returns, merge, simulate
from rblxmc.stats import summary, weighted_stats

CFG = Path(__file__).resolve().parents[1] / "configs" / "rblx_2026-09.toml"


@pytest.fixture(scope="module")
def cfg():
    return ModelConfig.load(CFG)


def no_lit(cfg):
    return replace(cfg, lit_lam=0.0)


def base_of(cfg):
    return next(s for s in cfg.scenarios if s.key == "base")


@pytest.fixture(scope="module")
def base_run(cfg):
    c = no_lit(cfg)
    sc = base_of(c)
    return c, sc, c.derive(sc), simulate(c, sc, c.derive(sc), 40_000, chunk=10_000, seed=1, dtype=torch.float64)


def test_config_derivation(cfg):
    assert abs(cfg.ev0 - 23.91) < 0.01
    assert abs(sum(cfg.schedule) - 0.75) < 1e-12
    for sc in cfg.scenarios:
        d = cfg.derive(sc)
        assert d.px_anchor > 0
        assert abs(d.gap_means[0] - cfg.schedule[0] * d.ln_a) < 1e-12
        if sc.bookings27 and sc.fcf_margin27:
            assert abs(sc.fcf27 - sc.bookings27 * sc.fcf_margin27) < 1e-9


def test_median_hits_anchor_without_litigation(base_run):
    """Drift calibration: with the (uncompensated) litigation factor off, the median terminal
    price lands on the anchor even with GARCH, t-gaps, the macro factor and bookings dispersion."""
    c, sc, der, out = base_run
    med = float(out.term.median())
    assert abs(med / der.px_anchor - 1) < 0.02, (med, der.px_anchor)


def test_litigation_is_an_expected_drag(cfg):
    sc = base_of(cfg)
    der = cfg.derive(sc)
    out = simulate(cfg, sc, der, 40_000, chunk=10_000, seed=2)
    med = float(out.term.median())
    fs = cfg.factor_summary(sc)
    assert med < der.px_anchor                       # drag, not compensated
    assert abs(med / der.px_anchor - 1) < fs["lit_expected_drag"] + 0.02


def test_touch_probability_consistent_with_bridge_minimum(base_run):
    c, sc, der, out = base_run
    for b, tp in out.touch.items():
        sampled = float((out.rmin <= b).float().mean())
        assert 0.0 <= float(tp.mean()) <= 1.0
        assert abs(float(tp.mean()) - sampled) < 0.03, (b, float(tp.mean()), sampled)


def test_shapes(base_run):
    c, sc, der, out = base_run
    assert out.term.shape == (40_000,)
    assert out.bands.shape == (40_000, len(c.grid))
    assert c.grid[-1] == c.n_steps


def test_deterministic(cfg):
    sc = cfg.scenarios[0]
    a = simulate(cfg, sc, cfg.derive(sc), 5_000, chunk=2_500, seed=7)
    b = simulate(cfg, sc, cfg.derive(sc), 5_000, chunk=2_500, seed=7)
    assert torch.equal(a.term, b.term)


def test_merge_across_ranks(cfg):
    c = no_lit(cfg)
    sc = c.scenarios[1]
    der = c.derive(sc)
    parts = [simulate(c, sc, der, 10_000, chunk=10_000, seed=3, rank=r) for r in range(4)]
    m = merge(parts)
    assert m.n == 40_000
    assert abs(float(m.term.median()) / der.px_anchor - 1) < 0.03


def test_garch_unconditional_variance():
    """With GARCH on, the average conditional variance equals sigma^2 dt and the shape is fat-tailed."""
    p = ShockParams(sigma=0.40, nu=6, earn_sd=0.0, gap_nu=0, lam=0.0, jm=0.0, js=0.0,
                    alpha=0.06, beta=0.90, gamma=0.04)
    r = horizon_log_returns(p, 40_000, [252], 1 / 252, earn_offsets=[], seed=5)[252].numpy()
    assert abs(r.std() / 0.40 - 1) < 0.05


def test_weighted_stats_matches_percentile(cfg):
    rng = np.random.default_rng(0)
    parts = [rng.lognormal(3.7, 0.4, 50_000) for _ in range(3)]
    w = weighted_stats(parts, [1 / 3] * 3, cfg.spot, cfg.street_avg)
    pooled = np.concatenate(parts)
    assert abs(w["median"] - np.percentile(pooled, 50)) < 0.05
    assert abs(w["p5"] - np.percentile(pooled, 5)) < 0.05


def test_summary_keys(base_run):
    c = base_run[0]
    s = summary(base_run[3].term.numpy(), c.spot)
    for k in ("p5", "p50", "p95", "P_gain", "P_halved", "CVaR95", "realised_vol"):
        assert k in s
