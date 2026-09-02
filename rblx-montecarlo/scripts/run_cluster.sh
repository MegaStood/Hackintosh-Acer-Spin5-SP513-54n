#!/usr/bin/env bash
# Several DGX Sparks, one process per node, NCCL over the ConnectX-7 link.
#
#   On spark-1 (master):  NODE_RANK=0 MASTER_ADDR=<spark-1 ip> ./scripts/run_cluster.sh
#   On spark-2:           NODE_RANK=1 MASTER_ADDR=<spark-1 ip> ./scripts/run_cluster.sh
#
# Set NNODES to the cluster size.  Every node must see the same repo path and config.
# If NCCL picks the wrong interface, set NCCL_SOCKET_IFNAME to the 200GbE port
# (e.g. enp1s0f0np0) and, for RoCE, leave NCCL_IB_DISABLE unset.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${MASTER_ADDR:?set MASTER_ADDR to the rank-0 node's IP}"
NNODES="${NNODES:-2}"
NODE_RANK="${NODE_RANK:-0}"
MASTER_PORT="${MASTER_PORT:-29500}"
PATHS="${PATHS:-20000000}"       # total per scenario, split across nodes
OUT="${OUT:-out}"
export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
export TORCH_CPP_LOG_LEVEL=ERROR
torchrun \
  --nnodes="$NNODES" --nproc_per_node=1 --node_rank="$NODE_RANK" \
  --master_addr="$MASTER_ADDR" --master_port="$MASTER_PORT" \
  -m rblxmc.run --config configs/rblx_2026-09.toml --paths "$PATHS" --out "$OUT" "$@"
