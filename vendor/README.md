# vendor/

Vendored third-party dependencies. Bundled here so `dbpd_analyzer` is
self-contained — no submodules, no external path hacks.

## `vendor/pycosh/`

Reference implementation of the correlated self-heterodyne (COSH) analysis
from Yuan, Wang, Liu, et al. *Opt. Express* **30**, 25147 (2022).

- **Upstream**: original release by **Maodong Gao** (2022)
- **License**: MIT — preserved verbatim in `vendor/pycosh/LICENSE`
- **Files**: `CoshConfig.py`, `CoshXcorr.py`, `__init__.py`
- **No local modifications** — vendored as-is to avoid drift from upstream

If upstream releases a meaningful update, replace these files and bump the
provenance note here.

## `vendor/sds7404/`

Minimal pyvisa-based driver for the Siglent SDS7404A H12 oscilloscope
(12-bit, 4 GHz, 20 GSa/s, 4 channels), reading multichannel waveforms over
LAN. Author: this project (wux_mac).

- **License**: MIT (root `LICENSE` applies)
- **Public surface**: `SDS7404` context-managed handle returning a
  `MultiChannelFrame` from `read_channels(...)`
- Tested against the SDS7404A H12 firmware shipped Q4-2024 / Q1-2025; the
  binary `:WAVeform:PREamble?` layout follows the SDS HD-generation 346-byte
  preamble (same as SDS6000A / SDS800XHD)

For other Siglent SDS HD-generation scopes, this driver should work
unchanged. For other vendors, replace this directory with an equivalent
driver exposing `read_channels(channels) -> object_with_voltages_dict_and_time_axis`.
