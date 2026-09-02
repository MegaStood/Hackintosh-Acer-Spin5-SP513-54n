"""rblxmc — three-scenario jump-diffusion Monte Carlo for a single equity, CPU or CUDA,
single node or torch.distributed.  Import is lazy: ``rblxmc.config`` and ``rblxmc.aux`` need
only NumPy; ``rblxmc.engine`` needs PyTorch."""

from .config import ModelConfig, Scenario  # noqa: F401

__version__ = "0.3.0"
