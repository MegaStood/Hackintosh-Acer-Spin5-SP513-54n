"""Summary statistics on the main rank (NumPy).  Schema matches what the report expects."""
from __future__ import annotations

import numpy as np

PCTS = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def _key(x: float) -> str:
    return f"{x:g}".replace(".", "")


def summary(x: np.ndarray, spot: float, street_avg: float = 48.32) -> dict:
    x = np.asarray(x, dtype=np.float64)
    d = {f"p{p}": float(np.percentile(x, p)) for p in PCTS}
    q5 = np.percentile(x, 5)
    d.update(
        mean=float(x.mean()),
        exp_ret=float(x.mean() / spot - 1),
        med_ret=float(np.median(x) / spot - 1),
        P_gain=float((x > spot).mean()),
        P_gt_4832=float((x > street_avg).mean()),
        P_gt_70=float((x > 70).mean()),
        P_gt_100=float((x > 100).mean()),
        P_lt_3388=float((x < 33.88).mean()),
        P_lt_25=float((x < 25).mean()),
        P_lt_20=float((x < 20).mean()),
        P_halved=float((x < spot / 2).mean()),
        P_doubled=float((x > 2 * spot).mean()),
        VaR95=float(q5 / spot - 1),
        CVaR95=float(x[x <= q5].mean() / spot - 1),
        realised_vol=float(np.std(np.log(x / spot))),
    )
    return d


def histogram(x: np.ndarray, bins: int, rng: tuple[float, float]) -> list[int]:
    """Counts on a fixed grid; values beyond the range pile into the edge bins (the report
    annotates the off-axis mass explicitly rather than drawing it)."""
    return np.histogram(np.clip(x, *rng), bins, rng)[0].tolist()


def band_percentiles(bands: np.ndarray, pcts=(5, 25, 50, 75, 95)) -> dict[str, list[float]]:
    return {f"p{p}": np.percentile(bands, p, axis=0).round(2).tolist() for p in pcts}


def touch_key(b: float) -> str:
    return "P_touch_" + _key(b)


def mixture_sample(parts: list[np.ndarray], weights, n: int, rng: np.random.Generator) -> np.ndarray:
    pick = rng.choice(len(parts), n, p=np.asarray(weights) / np.sum(weights))
    out = np.empty(n)
    for i, p in enumerate(parts):
        m = pick == i
        out[m] = rng.choice(p, int(m.sum()), replace=True)
    return out


def weighted_stats(parts: list[np.ndarray], weights, spot: float, street_avg: float) -> dict:
    """Exact weighted quantiles/probabilities over pooled per-scenario terminals."""
    x = np.concatenate(parts)
    w = np.concatenate([np.full(len(p), wi / len(p)) for p, wi in zip(parts, weights)])
    o = np.argsort(x)
    x, w = x[o], w[o]
    cdf = np.cumsum(w)
    cdf /= cdf[-1]
    q = lambda p: float(x[np.searchsorted(cdf, p)])
    tot = w.sum()
    pgt = lambda v: float(w[x > v].sum() / tot)
    lx = np.log(x / spot)
    mean_l = float(np.sum(w * lx) / tot)
    logvol = float(np.sqrt(np.sum(w * (lx - mean_l) ** 2) / tot))
    med = q(0.5)
    return dict(
        median=med, med_ret=med / spot - 1, p5=q(0.05), p50=med, p95=q(0.95), logvol=logvol,
        P_gain=pgt(spot), P_gt_4832=pgt(street_avg), P_halved=float(w[x < spot / 2].sum() / tot),
    )


def risk_neutral(spot: float, r: float, iv: float, t: float, n: int, rng: np.random.Generator, street_avg: float) -> dict:
    x = spot * np.exp((r - 0.5 * iv ** 2) * t + iv * np.sqrt(t) * rng.standard_normal(n))
    return summary(x, spot, street_avg) | {"iv": float(iv)}
