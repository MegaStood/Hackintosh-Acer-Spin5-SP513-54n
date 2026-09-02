"""Model configuration: market state, scenarios and the calibration derived from them.

Everything the simulation needs is declared in a TOML file (see ``configs/``) and
loaded into :class:`ModelConfig`.  Nothing in the engine is RBLX-specific; the
ticker lives entirely in the config.
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    """One fundamental scenario and the shock parameters that apply inside it."""

    key: str
    name: str
    prob: float
    fcf27: float        # forward free cash flow used for the anchor, $bn
    mult: float         # exit multiple applied to it
    sigma: float        # annualised log-vol of the EV diffusion (EV space)
    earn_sd: float      # sd of each scheduled earnings gap, log EV
    lam: float          # Poisson headline-jump intensity, per year
    jm: float           # mean log size of a headline jump
    js: float           # sd of log size of a headline jump
    cash_t: float       # terminal net cash, $bn (linear ramp from today's)


@dataclass(frozen=True)
class Derived:
    """Calibration outputs for one scenario."""

    ev_anchor: float    # median terminal EV, $bn  (= fcf27 * mult)
    px_anchor: float    # implied 12-month price anchor, per share
    ln_a: float         # ln(ev_anchor / ev0)
    q3_mean: float      # mean of the first earnings gap (regime resolution)
    mu: float           # continuous drift so the MEDIAN terminal EV hits the anchor


@dataclass
class ModelConfig:
    asof: str
    spot: float
    shares0: float          # bn shares outstanding today
    net_cash0: float        # $bn net cash today (cash + investments - debt)
    scenarios: list[Scenario]
    dilution: float = 0.025             # net share issuance over the horizon
    frac_q3: float = 0.40               # share of ln(anchor/ev0) realised at the first print
    horizon_years: float = 1.0
    n_steps: int = 252
    earn_days: tuple[int, ...] = (41, 110, 165, 228)
    nu: int = 6                         # Student-t degrees of freedom (integer)
    rf: float = 0.04
    iv30: float = 0.57                  # quoted 30-day implied vol, for the benchmark column
    barriers: tuple[float, ...] = (25.0, 33.88)
    street_avg: float = 48.32
    hist_bins: int = 90
    hist_range: tuple[float, float] = (0.0, 180.0)
    band_stride: int = 5
    weight_sets: list[tuple[str, tuple[float, float, float]]] = field(default_factory=list)
    vsens_factors: tuple[float, ...] = (0.75, 1.0, 1.25)

    # ------------------------------------------------------------------ derived
    @property
    def ev0(self) -> float:
        return self.spot * self.shares0 - self.net_cash0

    @property
    def evps0(self) -> float:
        return self.ev0 / self.shares0

    @property
    def dt(self) -> float:
        return self.horizon_years / self.n_steps

    @property
    def grid(self) -> list[int]:
        """Day indices at which fan-chart bands are recorded (always includes the last day)."""
        g = list(range(0, self.n_steps, self.band_stride))
        if g[-1] != self.n_steps:
            g.append(self.n_steps)
        return g

    @property
    def lev_spot(self) -> float:
        """EV / (EV + cash) at spot: the factor that maps EV vol to equity vol today."""
        return self.ev0 / (self.spot * self.shares0)

    def derive(self, sc: Scenario) -> Derived:
        t = self.horizon_years
        ev_anchor = sc.fcf27 * sc.mult
        ln_a = math.log(ev_anchor / self.ev0)
        q3_mean = self.frac_q3 * ln_a
        mu = ((1.0 - self.frac_q3) * ln_a - sc.lam * t * sc.jm) / t + 0.5 * sc.sigma ** 2
        px_anchor = (ev_anchor + sc.cash_t) / (self.shares0 * (1.0 + self.dilution))
        return Derived(ev_anchor, px_anchor, ln_a, q3_mean, mu)

    def scaled(self, sc: Scenario, f: float) -> Scenario:
        """Scenario with every within-scenario shock (diffusion, gaps, jump size) scaled by ``f``."""
        return replace(sc, sigma=sc.sigma * f, earn_sd=sc.earn_sd * f, js=sc.js * f)

    def event_adjusted_iv(self) -> float:
        """30-day IV with the four modelled gaps added: an event-consistent 1-year level."""
        base = next(s for s in self.scenarios if s.key == "base")
        gap_eq = base.earn_sd * self.lev_spot
        return math.sqrt(self.iv30 ** 2 + len(self.earn_days) * gap_eq ** 2)

    # ------------------------------------------------------------------ io
    @staticmethod
    def load(path: str | Path) -> "ModelConfig":
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        m = dict(raw["model"])
        scenarios = [Scenario(**s) for s in raw["scenario"]]
        wsets = [(w["label"], tuple(w["weights"])) for w in raw.get("weight_set", [])]
        for k in ("earn_days", "barriers", "hist_range", "vsens_factors"):
            if k in m:
                m[k] = tuple(m[k])
        cfg = ModelConfig(scenarios=scenarios, weight_sets=wsets, **m)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if abs(sum(s.prob for s in self.scenarios) - 1.0) > 1e-9:
            raise ValueError("scenario probabilities must sum to 1")
        if int(self.nu) != self.nu or self.nu < 3:
            raise ValueError("nu must be an integer >= 3 (chi-square built from normals)")
        if max(self.earn_days) >= self.n_steps:
            raise ValueError("earnings day index outside the horizon")
        if self.ev0 <= 0:
            raise ValueError("enterprise value must be positive")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scenarios"] = [asdict(s) for s in self.scenarios]
        return d
