#!/usr/bin/env bash
#SBATCH --job-name=rblxmc
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:30:00
#SBATCH --output=rblxmc-%j.log
# For Sparks managed by Slurm.  Each task is one rank; torchrun-style env is set by hand.
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export MASTER_ADDR="$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)"
export MASTER_PORT=29500
export WORLD_SIZE="$SLURM_NTASKS"
srun --export=ALL bash -c '
  export RANK=$SLURM_PROCID LOCAL_RANK=$SLURM_LOCALID
  python -m rblxmc.run --config configs/rblx_2026-09.toml --paths ${PATHS:-40000000} --out out
'
