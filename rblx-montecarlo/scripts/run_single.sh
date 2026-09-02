#!/usr/bin/env bash
# One machine: uses the GPU if PyTorch sees one, otherwise the CPU.
# On a DGX Spark 2M paths/scenario is a few seconds; on a laptop CPU use ~200k.
set -euo pipefail
cd "$(dirname "$0")/.."
PATHS="${PATHS:-2000000}"
OUT="${OUT:-out}"
python -m rblxmc.run --config configs/rblx_2026-09.toml --paths "$PATHS" --out "$OUT" "$@"
