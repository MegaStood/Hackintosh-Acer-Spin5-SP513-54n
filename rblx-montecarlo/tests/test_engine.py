"""Sanity tests on the CPU backend (small path counts; loose but meaningful tolerances)."""
from pathlib import Path

import numpy as np
import pytest
import torch

from rblxmc.config import ModelConfig
from rblxmc.engine import merge, simulate
from rblxmc.stats import summary, weighted_stats

CFG = Path(__file__).resolve().parents[1] / "configs" / "rblx_2026-09.toml"


@pytest.fixture(scope="module")
def cfg():
    return ModelConfig.load(CFG)


@pytest.fixture(scope="module")
def base_run(cfg):
    sc = next(s for s in cfg.scenarios if s.key == "base")
    return sc, cfg.derive(sc), simulate(cfg, sc, cfg.derive(sc), 40_000, chunk=10_000, seed=1, dtype=torch.float64)


def test_config_derivation(cfg):
    assert abs(cfg.ev0 - 23.91) < 0.01
    for sc in cfg.scenarios:
        d = cfg.derive(sc)
        assert d.px_anchor > 0 and abs(d.q3_mean - cfg.frac_q3 * d.ln_a) < 1e-12


def test_median_hits_anchor(cfg, base_run):
    """Drift calibration: the median terminal price lands on the scenario anchor."""
    sc, der, out = base_run
    med = float(out.term.median())
    assert abs(med / der.px_anchor - 1) < 0.02, (med, der.px_anchor)


def test_touch_probability_is_at_least_close_based(cfg, base_run):
    sc, der, out = base_run
    for b, tp in out.touch.items():
        close_based = float((out.rmin <= b).float().mean())      # rmin includes bridge minima
        assert 0.0 <= float(tp.mean()) <= 1.0
        # the analytic bridge probability and the sampled bridge minimum must agree closely
        assert abs(float(tp.mean()) - close_based) < 0.03, (b, float(tp.mean()), close_based)


def test_shapes(cfg, base_run):
    sc, der, out = base_run
    assert out.term.shape == (40_000,)
    assert out.bands.shape == (40_000, len(cfg.grid))
    assert cfg.grid[-1] == cfg.n_steps


def test_deterministic(cfg):
    sc = cfg.scenarios[0]
    a = simulate(cfg, sc, cfg.derive(sc), 5_000, chunk=2_500, seed=7)
    b = simulate(cfg, sc, cfg.derive(sc), 5_000, chunk=2_500, seed=7)
    assert torch.equal(a.term, b.term)


def test_merge_equals_single_run_in_distribution(cfg):
    """Splitting paths across 'ranks' (different seeds per rank) must not bias the median."""
    sc = cfg.scenarios[1]
    der = cfg.derive(sc)
    parts = [simulate(cfg, sc, der, 10_000, chunk=10_000, seed=3, rank=r) for r in range(4)]
    m = merge(parts)
    assert m.n == 40_000
    assert abs(float(m.term.median()) / der.px_anchor - 1) < 0.03


def test_weighted_stats_matches_percentile(cfg):
    rng = np.random.default_rng(0)
    parts = [rng.lognormal(3.7, 0.4, 50_000) for _ in range(3)]
    w = weighted_stats(parts, [1 / 3] * 3, cfg.spot, cfg.street_avg)
    pooled = np.concatenate(parts)
    assert abs(w["median"] - np.percentile(pooled, 50)) < 0.05
    assert abs(w["p5"] - np.percentile(pooled, 5)) < 0.05


def test_summary_keys(cfg, base_run):
    s = summary(base_run[2].term.numpy(), cfg.spot)
    for k in ("p5", "p50", "p95", "P_gain", "P_halved", "CVaR95", "realised_vol"):
        assert k in s
