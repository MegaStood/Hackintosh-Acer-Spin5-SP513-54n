# rblxmc — three-scenario jump-diffusion Monte Carlo

A single-equity valuation engine: three fundamental scenarios, each simulated as a
jump-diffusion on **enterprise value** (Student-t diffusion, scheduled earnings gaps with
regime resolution at the first print, compound-Poisson headline jumps), converted to a
share price through net cash and a diluting share count. Produces `results.json`, the
auxiliary chart data, and the self-contained HTML report.

One code path, written in PyTorch, runs on:

| Where | Command | 200k paths/scenario | 20M paths/scenario |
|---|---|---|---|
| laptop CPU | `scripts/run_single.sh` (`--device cpu`) | ~2 min | don't |
| one DGX Spark (GB10) | `scripts/run_single.sh` | seconds | ~1–2 min |
| N Sparks over ConnectX-7 | `scripts/run_cluster.sh` | – | ÷N |

The model, its calibration and every parameter are documented in the report itself
(`out/rblx-monte-carlo.html` after a run) and in `configs/rblx_2026-09.toml`.

## Install on a DGX Spark

DGX Spark is **aarch64 + CUDA 13** (Grace Blackwell GB10, 128 GB unified memory). Two
routes; the container is the one NVIDIA tests.

**A. NGC PyTorch container (recommended)**

```bash
git clone <this repo> && cd Hackintosh-Acer-Spin5-SP513-54n/rblx-montecarlo
docker run --gpus all --ipc=host --network host -it --rm \
  -v "$PWD":/work -w /work nvcr.io/nvidia/pytorch:25.09-py3 bash
pip install -e ".[test]"
python -c "import torch; print(torch.cuda.get_device_name(0))"   # NVIDIA GB10
```

**B. Native (DGX OS, Python ≥ 3.11)**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu130   # aarch64 CUDA-13 wheels
pip install -e ".[test]"
```

Anywhere else (x86 laptop, no GPU): `pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -e .`

## Run

```bash
# single node — GPU if visible, else CPU
python -m rblxmc.run --config configs/rblx_2026-09.toml --paths 2000000 --out out
open out/rblx-monte-carlo.html

# two Sparks: same command on both, one process per node
# spark-1:  NNODES=2 NODE_RANK=0 MASTER_ADDR=10.0.0.1 PATHS=20000000 scripts/run_cluster.sh
# spark-2:  NNODES=2 NODE_RANK=1 MASTER_ADDR=10.0.0.1 PATHS=20000000 scripts/run_cluster.sh
```

Flags worth knowing:

| flag | default | note |
|---|---|---|
| `--paths` | 200000 | total per scenario, split evenly across ranks |
| `--chunk` | 250000 | paths per GPU batch; ~4 GB at 250k on float32 — raise to 1M on a Spark |
| `--dtype` | float32 | `float64` for bit-for-bit comparison with the NumPy reference |
| `--vsens-paths` | 60000 | paths for the ±25% shock-scale sensitivity runs |
| `--device` | auto | `cpu` to force the CPU even on a GPU box |
| `--no-aux` | – | skip tails/path data and the HTML (results.json only) |

`torchrun` sets `RANK`/`WORLD_SIZE`/`LOCAL_RANK`; the script reads them. Each rank simulates
its share with its own seed stream, the main rank gathers the terminals (a few MB per
scenario even at 10M paths) and computes every statistic once. Fan-chart bands keep a
100k-path reservoir; everything else is exact over all paths.

## How the cluster path works

- **Backend:** NCCL when CUDA is visible (RoCE over the 200 GbE QSFP link between Sparks),
  Gloo on CPU. If NCCL binds the wrong NIC: `export NCCL_SOCKET_IFNAME=<cx7 interface>`.
- **Collectives:** exactly one — `gather_object` of each rank's outputs to rank 0 after each
  scenario. No all-reduce inside the kernel, so scaling is linear in nodes.
- **Reproducibility:** the seed stream is `(seed, scenario, rank, chunk)`, so the same
  `--paths --chunk` on the same world size reproduces bit-for-bit (float64) on CPU and GPU.
- **Memory:** per chunk the kernel holds ~6 arrays of `chunk × 252` floats plus the
  Student-t chi-square build (`nu` extra normals). On a Spark `--chunk 1000000` is comfortable.

## Layout

```
rblxmc/config.py        ModelConfig / Scenario, TOML loader, drift + anchor calibration
rblxmc/engine.py        the kernel (torch): diffusion, gaps, jumps, cash floor, dilution,
                        Brownian-bridge touch probabilities and interval minima
rblxmc/stats.py         percentiles, histograms, weighted quantiles, mixture, RN benchmarks
rblxmc/distributed.py   torch.distributed init / split / gather
rblxmc/run.py           CLI orchestration; writes results.json
rblxmc/aux.py           tails.json (t vs Gaussian, kurtosis by horizon) and path.json (anatomy)
rblxmc/report/build.py  results.json + tails.json + path.json -> rblx-monte-carlo.html
configs/                the RBLX 2026-09 parameter set
reference/              the original NumPy scripts the engine was validated against
tests/                  pytest sanity suite (CPU, ~1 min)
```

## Validate

```bash
pytest -q                      # median hits anchor, bridge stats consistent, determinism, merge
python reference/mc.py         # NumPy reference (writes results.json next to itself)
```

At 200k paths and float64 the engine and `reference/mc.py` agree on every reported
statistic to Monte Carlo noise (different RNG streams, so not bit-identical).

## Changing the ticker

Everything ticker-specific is in the TOML: spot, shares, net cash, dilution, scenario
anchors (`fcf27 × mult`), shock sizes, earnings-day indices, barriers. The report's copy in
`report/build.py` is RBLX-specific prose and would need rewriting for another name; the
engine and `results.json` would not.
