# rblxmc — refined three-scenario Monte Carlo for a single equity

A valuation engine built for one DGX Spark (and scaling to several): three fundamental
scenarios, each simulated as a jump-diffusion on **enterprise value** and converted to a
share price through net cash and a diluting share count. Version 4 adds the things a
reviewer would ask for after reading the first report:

| component | what it does | where the numbers come from |
|---|---|---|
| GJR-GARCH(1,1) diffusion | volatility clusters; drawdowns compound | `calibrate` fits α, β, γ from history |
| Student-t earnings gaps | print moves are fat-tailed (−18%, −29% lately) | `calibrate` fits sd and dof from print days |
| repricing schedule | 40/20/10/5% of each scenario's revaluation lands *at* the prints | judgment (disclosed) |
| **macro factor** | 10y-yield deviation (OU) priced through a multiple duration | `calibrate` fits from a yield series; default is a 2022-style beta |
| **bookings chain** | anchors = FY27 bookings × FCF margin × multiple, with per-path forecast dispersion revealed at the prints | judgment + analyst dispersion |
| **litigation event** | Poisson-timed resolution: cash outflow (capped at available net cash) + EV sentiment hit, scenario-scaled, *not* compensated in the drift | order-of-magnitude priors |
| Brownian-bridge path stats | touch probabilities and drawdowns with intraday crossings | — |
| `calibrate` | fits every shock parameter from a price CSV (or yfinance) | — |
| `backtest` | rolling PIT / coverage test of the engine's 1-week / 1-month / 3-month bands | — |

One code path, written in PyTorch, runs on a laptop CPU, one Spark, or N Sparks under `torchrun`.

## Install on a DGX Spark

DGX Spark is **aarch64 + CUDA 13** (GB10, 128 GB unified memory).

```bash
# A. NGC container (the route NVIDIA tests)
docker run --gpus all --ipc=host --network host -it --rm \
  -v "$PWD":/work -w /work nvcr.io/nvidia/pytorch:25.09-py3 bash
pip install -e ".[test,data]"

# B. native
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu130
pip install -e ".[test,data]"
```

Anywhere without a GPU: `pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -e .`

## The three commands

```bash
# 1. calibrate the shock parameters from history (anchors/weights are copied through)
python -m rblxmc.calibrate --ticker RBLX --start 2022-01-01 \
    --config configs/rblx_2026-09.toml --out configs/rblx_calibrated.toml \
    [--yields data/DGS10.csv] [--earnings 2026-07-31 2026-05-01 ...]
#    prints a judgment-vs-fitted table; --csv data/RBLX.csv works offline

# 2. check the engine's shape and volatility out of sample
python -m rblxmc.backtest --ticker RBLX --config configs/rblx_calibrated.toml \
    --horizons 5 21 63 --step 5 --paths 20000
#    KS p-value > 0.05 and ~90% inside the 90% band means the engine is honest

# 3. run the valuation and build the report
python -m rblxmc.run --config configs/rblx_calibrated.toml --paths 2000000 --out out
open out/rblx-monte-carlo.html
```

On a Spark step 3 takes seconds at 2M paths/scenario (`--chunk` defaults to 1M on CUDA);
20M paths is a couple of minutes. Two Sparks over the ConnectX-7 link:

```bash
# same command on each node, one process per node
NNODES=2 NODE_RANK=0 MASTER_ADDR=<spark-1 ip> PATHS=20000000 scripts/run_cluster.sh
NNODES=2 NODE_RANK=1 MASTER_ADDR=<spark-1 ip> PATHS=20000000 scripts/run_cluster.sh
```

Flags: `--paths` (per scenario, split across ranks) · `--chunk` (0 = auto) · `--dtype float64`
for bit-level comparison with the NumPy reference · `--device cpu` to force the CPU ·
`--no-aux` for `results.json` only · `--vsens-paths` for the shock-scale sensitivity runs.

## What is judgment and what is fitted

Everything is in `configs/rblx_2026-09.toml`, grouped and commented. Fitted by `calibrate`:
diffusion σ (overall and by drawdown/normal regime), earnings-gap sd and dof, headline-jump
intensity / mean / sd, Student-t dof, GARCH α β γ, and — with a yield series — the rate
duration, vol, mean reversion and correlation. Judgment, copied through: the scenario anchors
(`bookings27 × fcf_margin27 × mult`), weights, terminal cash, dilution, repricing schedule,
bookings dispersion, and the litigation prior. The report says which is which.

## Validation done here (CPU; no GPU in the authoring sandbox)

- `pytest -q` — 12 tests: median hits the anchor with all factors on and litigation off;
  litigation is a drag bounded by its expected value; bridge touch probability agrees with
  the sampled bridge minimum; determinism; multi-rank merge; GARCH unconditional variance;
  **parameter recovery** on 8 synthetic years (σ within 12%, gap sd within 30%, GARCH
  persistence within 0.12, ν within range); **backtest coverage** on synthetic data
  (90% band covers 90% ± 6%, KS p > 0.01).
- v3 (no factors, constant vol) agrees with `reference/mc.py` on every statistic to Monte
  Carlo noise; a 2-process gloo `torchrun` reproduces the single-process results.
- The CUDA/NCCL path is the same code with `device="cuda"`; it was not executed on a GB10
  here. If NCCL binds the wrong NIC set `NCCL_SOCKET_IFNAME`.

## Layout

```
rblxmc/config.py        ModelConfig / Scenario, TOML loader, anchor + drift calibration, factor sizes
rblxmc/engine.py        the kernel: GARCH t-diffusion, t-gaps w/ repricing schedule, Poisson jumps,
                        macro OU factor, bookings dispersion, litigation event, cash floor, dilution,
                        Brownian-bridge touch + interval minima
rblxmc/calibrate.py     history -> shock parameters -> calibrated TOML (+ judgment/fitted table)
rblxmc/backtest.py      rolling PIT / coverage test (KS, 50/90/98% bands)
rblxmc/stats.py         percentiles, weighted quantiles, mixture, risk-neutral benchmarks
rblxmc/distributed.py   torch.distributed init / split / gather
rblxmc/run.py           CLI; results.json (+ weights and shock-scale sensitivity tables)
rblxmc/aux.py           chart data (t vs Gaussian, kurtosis by horizon, path anatomy, P vs Q)
rblxmc/report/build.py  results.json + tails.json + path.json -> rblx-monte-carlo.html
configs/                RBLX 2026-09 parameter set (judgment); calibrate writes the fitted twin
scripts/                run_single.sh · run_cluster.sh · run_slurm.sbatch
reference/              the original NumPy scripts (v3) the engine was validated against
tests/                  pytest suite (~2 min on CPU)
```

Everything ticker-specific is in the TOML; the report's prose in `report/build.py` is
RBLX-specific and would need rewriting for another name.
