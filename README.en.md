# dbpd_analyzer

**English** | [简体中文](README.md)

Desktop UI for **dual-BPD correlated self-heterodyne (COSH)** laser
frequency-noise analysis. PySide6 + matplotlib + the
[pycosh](https://github.com/) reference implementation from Yuan et al.
(*Opt. Express* **30**, 25147, 2022).

Apple-light visual style, cross-platform (macOS / Windows / Linux), with
live oscilloscope acquisition (Siglent SDS7404), automatic linewidth
extraction (Lorentzian + β-separation), and MZI FSR self-calibration.

---

## Features

| | |
|---|---|
| **Two processing algorithms** | **BW-segmented · dual-BPD** (CoshXcorr cross-correlation, suppresses each BPD's independent electronic noise) / **multi-record averaging · single-BPD (Hann window)** (accumulate records toward the white-noise floor) |
| **Two data sources** | **File** / **scope** (Siglent SDS7404 over LAN). The data panel selects them on two axes: *algorithm tab × data source* |
| **Multi-record file I/O** | The averaging algorithm can save the N raw acquired records into **one `.npz`**, then reload and re-average them offline |
| **Multi-resolution Welch processing** | User-editable BW segments + offset-start ratio (dual-BPD algorithm only) |
| **FSR auto-calibrate / manual override** | Detect the MZI free-spectral range from a real beat trace and back-solve fiber length; or enter fiber length ΔL / delay τ manually (**arbitrary precision**, τ ↔ ΔL linked) |
| **Lorentzian floor fit** | Minimum of S<sub>ν</sub>(f) over a chosen high-offset band → FWHM<sub>L</sub> = π · S<sub>0</sub> (single-sideband) |
| **β-separation line + integration** | Di Domenico 2010 — overlay β-line and report the Gaussian FWHM from the area above it |
| **Export / save** | dual-BPD: **Export spectra** writes both S<sub>ν</sub>/S<sub>φ</sub> (all traces + error columns + metadata header) to one CSV; averaging: **Save averaged spectrum** saves the averaged spectrum + linewidth/FSR (CSV or npz) |
| **Live monitoring** (dual-BPD only) | After Acquire the scope resumes live; "▶ Monitor (live)" repeatedly acquires + processes single-shot frames, updating the spectrum and a Lorentz-linewidth-vs-time trend |

---

## The two algorithms

The data panel's top selector is the **algorithm**; the data source and
parameters below follow the active algorithm:

- **BW-segmented · dual-BPD (CoshXcorr)** — the standard correlated
  self-heterodyne cross-correlation. Needs two BPDs (C2/C4); multi-resolution
  Welch segments; cross-correlation cancels each channel's electronic noise.
  Sources:
  - **File** — one 3-column file `t, BPD1, BPD2` (csv/npy/npz)
  - **Scope** — acquire both channels at once
- **Multi-record averaging · single-BPD (Hann window)** — one BPD; accumulate
  `2·|rfft(Hann·phase)|²` over many measurements, divide by the MZI transfer
  function `G(f) = 4·sin²(πfτ)`, average toward the white frequency-noise floor.
  Sources:
  - **Scope** — acquire N times back-to-back and average (optionally keep the raw records to save)
  - **File** — load a multi-record `.npz` (N raw records) and re-average offline

> The averaging algorithm is a single curve, with no BW segments and no live
> monitoring, so switching to that tab hides the Segments box, the
> cross-correlation / per-BPD display toggles, and the Monitor + Export buttons.

---

## Project layout

```
dbpd_analyzer/
├── README.md / README.en.md   primary doc (Chinese) / English
├── LICENSE                  MIT (this project) + third-party notice
├── requirements.txt
├── main.py                  Entry point — Fusion style + global QSS + MainWindow
├── app/
│   ├── styles.py            Apple-light QSS
│   ├── data_io.py           CSV/npy/npz load + save + multi-record file I/O
│   ├── averaging.py         Multi-record averaging (single-BPD Hann): PsdAverager + save
│   ├── mzi_calibrate.py     FSR auto-calibration (Hilbert + Welch + dip search)
│   ├── analysis.py          Lorentz floor fit + β-separation integration
│   ├── scope.py             Workers: acquire / multi-average / file-average / connection test
│   ├── processor.py         CoshXcorr wrapper + ProcessWorker + CalibrateWorker
│   ├── monitor.py           Live-monitoring worker
│   ├── monitor_io.py        Per-cycle spectrum / trend persistence
│   ├── plot_widget.py       matplotlib FigureCanvas + analysis overlays
│   ├── settings_panel.py    Sidebar: data (algorithm × source) / optical / segments / display / analysis
│   └── main_window.py       Wires sidebar ↔ plot ↔ workers
├── vendor/
│   ├── pycosh/              MIT, Maodong Gao 2022 — COSH reference implementation
│   └── sds7404/             Siglent SDS7404A LAN driver (pyvisa)
└── examples/                sample data + notes
```

---

## Setup

```bash
git clone <repo-url>
cd dbpd_analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`pycosh` and the `sds7404` driver are bundled under `vendor/`; the app adds
`vendor/` to `sys.path` at import time. Scope connection uses **pyvisa-py**
(pure Python, installed via requirements) — no system NI-VISA required.

### Power-user overrides

```bash
export DBPD_PYCOSH_PARENT=/path/to/folder-containing-pycosh
export DBPD_SDS7404_PARENT=/path/to/folder-containing-sds7404
```

The override only takes effect after the bundled `vendor/` copy fails to
import, so a clean clone always works out of the box.

## Run

```bash
.venv/bin/python main.py            # macOS / Linux
.venv\Scripts\python.exe main.py    # Windows
```

---

## Workflow

### Mode A — dual-BPD (CoshXcorr) analysis

1. Top of the data panel → **BW-segmented · dual-BPD**
2. Data source → **File** (one 3-column file `t, BPD1, BPD2`) or **Scope**
   (set IP, BPD1=C2, BPD2=C4, ⏺ Acquire — a background QThread pulls the frame
   without freezing the UI and the scope resumes live afterwards)
3. **Optical path** → set n_core / AOM carrier; FSR auto-calibrates from the
   data, or tick **Manual FSR** to enter ΔL / τ (arbitrary precision)
4. Processing runs automatically once FSR calibration succeeds; after changing
   optical/segment settings, click ▶ **Process** to re-run
5. **Analysis** card shows Lorentz FWHM and β-integrated Gaussian FWHM live
6. **Export spectra…** writes one CSV with both S<sub>ν</sub> and S<sub>φ</sub>
   (all traces + error columns + full metadata header)
7. **▶ Monitor (live)** (after one Acquire has calibrated the FSR) repeatedly
   grabs and re-processes single-shot frames, updating the spectrum and a
   **Lorentz FWHM vs time** trend strip

### Mode B — multi-record averaging (single-BPD Hann)

1. Top of the data panel → **multi-record averaging · single-BPD (Hann)**
2. Source **Scope**: set IP, BPD1 channel, average count N, edge skip; tick
   **keep raw records** to later save the N raw traces with **Save raw records**
3. Click ▶ **Process** (or the panel's *Acquire ×N & average*): acquire N times,
   auto-calibrate FSR from the first frame (or use Manual FSR), accumulate + average
4. Source **File**: pick a multi-record `.npz` (saved earlier), ▶ Process to re-average offline
5. **Save averaged spectrum…** saves the averaged spectrum + n_avg/FSR/floor/linewidth (CSV or npz)

---

## How to read the spectrum

- **Cross-correlation curve (blue)** is the laser noise estimate with both BPDs' independent electronic noise suppressed
- **Lorentz floor (red dashed)** is the white-frequency-noise asymptote → FWHM<sub>L</sub> = π · S<sub>0</sub>
- **β-line (orange dotted)**: S<sub>ν</sub> = 8 ln(2) / π² · f ≈ 0.5615 · f. Above the line → Gaussian (slow) broadening; below → Lorentzian (fast). The integrated area gives FWHM<sub>G</sub> = √(8 ln 2 · A) (Di Domenico 2010)
- **Sharp peaks at n · FSR** are deconvolution singularities of the MZI transfer function (division by `2sin²(πf/FSR)→0`), independent of the window; prominent peaks usually mean the fiber length is wrong → click *Auto-calibrate FSR* or fix τ

---

## Measurement recommendations

These are the configurations we converged on for an 80 MHz AOM
self-heterodyne setup. Scale the numbers if your AOM carrier differs.

### A — Choosing the sample rate

Hilbert-based phase extraction loses validity as the analyzed offset
approaches the AOM carrier f<sub>c</sub>. The safe ceiling is roughly
**f<sub>c</sub> / 2**; above ~f<sub>c</sub> the Hilbert filter folds the lower
sideband and the result is meaningless.

```
sample_rate ≥ 2 · f_c          (Nyquist absolute minimum)
sample_rate ≥ 2.5 · f_c        (recommended, anti-alias margin)
```

For **80 MHz AOM**: minimum **200 MSa/s**, comfortable **250 MSa/s**.

### B — Choosing BW_SEGMENT (dual-BPD algorithm)

`BW_SEGMENT = [bw₀, bw₁, …]` (Hz) defines a multi-band Welch-style PSD
estimate; each band uses segment length `1 / (bw · dt)`. Constraints:

| Quantity | Constraint | Why |
|---|---|---|
| `bw[0]` | ≥ 5 / T | ≥ 5 segments at the lowest band for tolerable 1σ (~45%) |
| `bw[0] · ratio` | = f<sub>min</sub> | Default `offset_start_ratio = 10` |
| ratio between bands | 3× (smooth) or 10× (compact) | 3× → ~1.7× SNR jumps at band edges; 10× → ~3.2× |
| `bw[-1]` | > 2/τ (= 2 · FSR) | Last band enters the incoherent limit so G(f) doesn't amplify tones |
| `bw[-1] · ratio` | < f<sub>c</sub> / 2 | Don't analyze past Hilbert's valid range |

### C — Decision table for 80 MHz AOM, 7–10 m fiber

| Target | sample_rate | T | BW_SEGMENT (Hz) | offset range | Notes |
|---|---:|---:|---|---:|---|
| **Default** | 1 GSa/s | 5–10 ms | `1k, 3k, 10k, 30k, 100k, 300k, 1M, 3M, 10M` | 10 kHz – 30 MHz | Current working config |
| Compact / fast | 2 GSa/s | 1–5 ms | `10k, 30k, 100k, 300k, 1M, 3M, 10M, 30M` | 100 kHz – 30 MHz | Trades low-freq for record speed |
| Mid-band | 500 MSa/s | 0.5 s | `10, 30, 100, …, 10M` (12 bands) | 100 Hz – 30 MHz | Light acoustic isolation helpful |
| **Low-freq** | **200–250 MSa/s** | **5–10 s** | `1, 3, 10, 30, 100, 300, 1k, …, 1e7` (14 bands) | **10 Hz – 30 MHz** | **Needs ≥ 1 GSa memory + 100 m fiber + acoustic isolation** |
| Ultra-low-freq | 100–200 MSa/s | 60 s | `0.1, 0.3, 1, …, 1e6` | 1 Hz – 10 MHz | Multi-record averaging required |

### D — Memory math

`record_time = memory_depth_per_channel / sample_rate`

| Sample rate | 500 MSa | 1 GSa | 2 GSa |
|---:|---:|---:|---:|
| 500 MSa/s | 1.0 s | 2.0 s | 4.0 s |
| 250 MSa/s | 2.0 s | 4.0 s | 8.0 s |
| 200 MSa/s | 2.5 s | 5.0 s | 10.0 s |
| 100 MSa/s | 5.0 s | 10.0 s | 20.0 s |

For 80 MHz AOM, **200 MSa/s is the practical floor**, so achievable T is
bounded by per-channel memory.

### E — Lowest reachable offset by hardware

Using `bw[0] · T ≥ 5` and `sample_rate ≥ 200 MSa/s`:

| Memory | Max T (@ 200 MSa/s) | Lowest f_min (single-shot) |
|---:|---:|---:|
| 500 MSa | 2.5 s | ≈ 20 Hz |
| 1 GSa | 5 s | ≈ 10 Hz |
| 2 GSa | 10 s | ≈ 5 Hz |

To reach lower — use the **multi-record averaging** algorithm (above).

---

## Hardware checklist for low-frequency measurements

Below ~1 kHz offset these matter more than software tuning:

- **Delay fiber length** — short fiber (5–10 m) is fine above 10 kHz; low offsets need longer τ so G(f) doesn't crush the signal below the BPD noise floor:
  - 7–10 m: useful above 10 kHz
  - 100 m: down to ~100 Hz (~100× better low-freq sensitivity)
  - 1 km: down to ~1 Hz (Yuan 2022 used 1 km)
- **Acoustic isolation** — long fiber picks up vibration that masquerades as frequency noise: coil it in a foam/sorbothane-lined box, put it on a vibration-isolated table, kill HVAC/fans/pumps
- **Thermal isolation** — temperature drift modulates fiber delay; a double-walled enclosure helps for long records (> 10 s)
- **AOM drive cleanness** — 80 MHz driver harmonics/sidebands show up directly; use a clean RF source

---

## Oscilloscope connection notes

The driver uses `pyvisa` over LAN, defaulting to the **pyvisa-py (`@py`)
backend** with resource string `TCPIP0::<ip>::inst0::INSTR` (VXI-11).

- On macOS the system **NI-VISA can hang in `open_resource`** (its VXI-11
  negotiation is not fully compatible with Siglent), so it is not the default;
  to force it, pass `visa_backend="@ivi"` or your own `resource_manager`.
- If VXI-11 returns `VI_ERROR_RSRC_NFOUND`, a previous session likely left the
  single VXI-11 link slot occupied (reboot the scope to clear it), or LXI/VXI-11
  is disabled on the scope.
- The data panel has a **Test connection** button (`*IDN?`) to verify reachability first.

---

## Algorithm notes (pycosh)

For the underlying maths — Hilbert phase extraction, multi-band Welch PSD,
G(f) sinc² compensation (paper Eq. 22), cross-correlation suppression of BPD
noise (Eq. 27–28) — see `vendor/pycosh/CoshXcorr.py` and the Yuan 2022 paper.
Three non-obvious points:

- The AOM carrier is **not** a parameter; pycosh implicitly rejects it by
  skipping the DC bin (offsets start at `offset_start_ratio · bw[0]`)
- The app displays **single-sideband (SSB)** densities: pycosh emits a
  two-sided PSD, multiplied by 2 for display. In this convention
  Lorentz FWHM = π · S<sub>0</sub> and the β-line is 8 ln2/π² · f (Di Domenico 2010, one-sided)
- CoshXcorr uses a **rectangular window** (the original paper's method); the
  averaging algorithm uses a **Hann window** — the two are deliberately different

---

## License

MIT — see [LICENSE](LICENSE).

Bundled vendored components keep their own licensing:
- `vendor/pycosh/`  MIT, Copyright (c) 2022 Maodong Gao
- `vendor/sds7404/`  MIT (this project)
