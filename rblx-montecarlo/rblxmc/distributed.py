"""Thin wrapper over torch.distributed so the same script runs on one laptop CPU,
one DGX Spark, or several Sparks under ``torchrun``.

Contract: every rank simulates its share of the paths; the main rank gathers the
per-rank outputs and does all statistics.  Nothing else is collective.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import torch
import torch.distributed as dist


@dataclass
class Ctx:
    rank: int
    world: int
    local_rank: int
    device: torch.device
    enabled: bool

    @property
    def main(self) -> bool:
        return self.rank == 0


def init(device_pref: str = "auto") -> Ctx:
    world = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    use_cuda = torch.cuda.is_available() and device_pref in ("auto", "cuda")
    if use_cuda:
        torch.cuda.set_device(local_rank)
        device = torch.device("cuda", local_rank)
    else:
        device = torch.device("cpu")
    enabled = world > 1
    if enabled and not dist.is_initialized():
        dist.init_process_group(backend="nccl" if use_cuda else "gloo")
    return Ctx(rank, world, local_rank, device, enabled)


def split(n_total: int, ctx: Ctx) -> int:
    """This rank's share of ``n_total`` paths (remainder goes to the low ranks)."""
    base, rem = divmod(n_total, ctx.world)
    return base + (1 if ctx.rank < rem else 0)


def gather(obj, ctx: Ctx):
    """Gather arbitrary picklable objects to the main rank; returns a list there, None elsewhere."""
    if not ctx.enabled:
        return [obj]
    buf = [None] * ctx.world if ctx.main else None
    dist.gather_object(obj, buf, dst=0)
    return buf


def barrier(ctx: Ctx) -> None:
    if ctx.enabled:
        dist.barrier()


def finalize(ctx: Ctx) -> None:
    if ctx.enabled and dist.is_initialized():
        dist.destroy_process_group()
