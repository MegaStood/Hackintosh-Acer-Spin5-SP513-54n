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
    bookings27: float | None = None     # forward bookings, $bn (informational chain to fcf27)
    fcf_margin27: float | None = None   # FCF / bookings; fcf27 = bookings27 * margin if both given
    lit_scale: float = 1.0              # multiplies litigation intensity, loss and sentiment hit
    fund_scale: float = 1.0             # multiplies the bookings-forecast dispersion
    thesis: str = ""

    def __post_init__(self):
        if self.bookings27 is not None and self.fcf_margin27 is not None:
            object.__setattr__(self, "fcf27", self.bookings27 * self.fcf_margin27)


@dataclass(frozen=True)
class Derived:
    """Calibration outputs for one scenario."""

    ev_anchor: float    # median terminal EV, $bn  (= fcf27 * mult)
    px_anchor: float    # implied 12-month price anchor, per share
    ln_a: float         # ln(ev_anchor / ev0)
    q3_mean: float      # mean of the first earnings gap (regime resolution)
    mu: float           # continuous drift so the MEDIAN terminal EV hits the anchor
    gap_means: tuple[float, ...] = ()   # mean of every scheduled gap (repricing schedule x ln_a)


@dataclass
class ModelConfig:
    asof: str
    spot: float
    shares0: float          # bn shares outstanding today
    net_cash0: float        # $bn net cash today (cash + investments - debt)
    scenarios: list[Scenario]
    dilution: float = 0.025             # net share issuance over the horizon
    frac_q3: float = 0.40               # share of ln(anchor/ev0) realised at the first print
    repricing: tuple[float, ...] | None = None   # per-print shares; overrides frac_q3 if given
    garch_alpha: float = 0.0            # GJR-GARCH(1,1) on the diffusion leg; 0/0/0 = constant vol
    garch_beta: float = 0.0
    garch_gamma: float = 0.0
    gap_nu: int = 0                     # Student-t dof for earnings gaps; 0 = Gaussian
    # --- macro: 10y-yield deviation (OU) priced through a multiple duration
    rate_duration: float = 0.0          # % change in EV per 1.00 change in yield (e.g. 9 = -9% per +100bp)
    rate_kappa: float = 0.0             # mean reversion of the deviation, per year
    rate_vol: float = 0.0               # annual vol of the yield, in yield units (0.009 = 90bp)
    rate_rho: float = 0.0               # corr(yield shock, equity diffusion shock)
    # --- bookings / fundamental forecast dispersion
    fund_sd: float = 0.0                # log-sd of the per-path anchor deviation
    # --- litigation
    lit_lam: float = 0.0                # resolution intensity per year (P(within 1y) = 1 - e^-lam)
    lit_loss_mean: float = 0.0          # $bn cash outflow at resolution (lognormal mean)
    lit_loss_sdlog: float = 0.6
    lit_sent_mean: float = 0.0          # fractional EV sentiment hit at resolution
    lit_sent_sd: float = 0.0
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

    @property
    def schedule(self) -> tuple[float, ...]:
        """Share of the scenario's log-repricing delivered at each print."""
        if self.repricing is not None:
            return tuple(self.repricing)
        return (self.frac_q3,) + (0.0,) * (len(self.earn_days) - 1)

    def derive(self, sc: Scenario) -> Derived:
        t = self.horizon_years
        ev_anchor = sc.fcf27 * sc.mult
        ln_a = math.log(ev_anchor / self.ev0)
        sched = self.schedule
        gap_means = tuple(f * ln_a for f in sched)
        mu = ((1.0 - sum(sched)) * ln_a - sc.lam * t * sc.jm) / t + 0.5 * sc.sigma ** 2
        px_anchor = (ev_anchor + sc.cash_t) / (self.shares0 * (1.0 + self.dilution))
        return Derived(ev_anchor, px_anchor, ln_a, gap_means[0], mu, gap_means)

    def scaled(self, sc: Scenario, f: float) -> Scenario:
        """Scenario with every within-scenario shock (diffusion, gaps, jump size) scaled by ``f``."""
        return replace(sc, sigma=sc.sigma * f, earn_sd=sc.earn_sd * f, js=sc.js * f)

    def factor_summary(self, sc: Scenario) -> dict:
        """Rough size of each v4 factor over the horizon, for the report."""
        t = self.horizon_years
        k = self.rate_kappa
        var_x = (self.rate_vol ** 2) * ((1 - math.exp(-2 * k * t)) / (2 * k) if k > 0 else t)
        p_lit = 1 - math.exp(-self.lit_lam * sc.lit_scale * t)
        loss = self.lit_loss_mean * sc.lit_scale
        return dict(
            macro_logsd=self.rate_duration * math.sqrt(var_x),
            fund_logsd=self.fund_sd * sc.fund_scale,
            lit_prob=p_lit,
            lit_expected_drag=p_lit * (self.lit_sent_mean * sc.lit_scale + loss / (self.ev0 + self.net_cash0)),
        )

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
        for tbl in ("macro", "fundamentals", "litigation"):
            m.update(raw.get(tbl, {}))
        scenarios = [Scenario(**s) for s in raw["scenario"]]
        wsets = [(w["label"], tuple(w["weights"])) for w in raw.get("weight_set", [])]
        for k in ("earn_days", "barriers", "hist_range", "vsens_factors", "repricing"):
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
        if len(self.schedule) != len(self.earn_days):
            raise ValueError("repricing must have one entry per earnings day")
        if not 0 <= sum(self.schedule) <= 1:
            raise ValueError("repricing shares must sum to between 0 and 1")
        if self.garch_alpha + self.garch_beta + 0.5 * self.garch_gamma >= 1:
            raise ValueError("GARCH must be stationary: alpha + beta + gamma/2 < 1")
        if self.gap_nu and self.gap_nu < 3:
            raise ValueError("gap_nu must be 0 (Gaussian) or an integer >= 3")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scenarios"] = [asdict(s) for s in self.scenarios]
        return d
