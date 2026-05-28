"""Minimal Siglent SDS7404A H12 LAN driver (vendored).

Re-exports the SDS7404 handle and MultiChannelFrame dataclass so callers can
just do `from sds7404 import SDS7404, MultiChannelFrame`.
"""
from .sds7404 import (
    CHANNELS,
    MultiChannelFrame,
    SDS7404,
    WaveformPreamble,
)

__all__ = ["CHANNELS", "MultiChannelFrame", "SDS7404", "WaveformPreamble"]
