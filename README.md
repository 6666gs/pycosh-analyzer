# dbpd_analyzer

**English** | [简体中文](README.zh-CN.md)

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
| **Three data-source modes** | Single 3-column CSV · two-CSV (one per channel) · live acquire from Siglent SDS7404 over LAN |
| **Multi-resolution Welch processing** | User-editable BW segments + offset-start ratio; multi-band averaging for low-noise floors |
| **Live plot toggles** | S<sub>ν</sub> ↔ S<sub>φ</sub>, per-channel single PSD vs cross-correlation, error band |
| **Auto-calibrate FSR** | Detect the MZI free-spectral range from a real beat trace (Welch + Savitzky-Golay + dip search) and back-solve fiber length |
| **Lorentzian floor fit** | Minimum of S<sub>ν</sub>(f) over a chosen high-offset band → FWHM<sub>L</sub> = π · S<sub>0</sub> (single-sideband) |
| **β-separation line + integration** | Di Domenico 2010 method — overlay β-line and report the Gaussian FWHM from the area above it |
| **CSV export** | One click writes **both** frequency- and phase-noise spectra (all traces + error columns) to a single CSV with a full metadata header (delay, FSR, AOM, segments, linewidth fits) |

---

## Project layout

```
dbpd_analyzer/
├── README.md
├── LICENSE                  MIT (this project) + third-party notice
├── requirements.txt
├── main.py                  Entry point — Fusion style + global QSS + MainWindow
├── app/
│   ├── __init__.py
│   ├── styles.py            Apple-light QSS
│   ├── data_io.py           CSV load / array load / save
│   ├── mzi_calibrate.py     FSR auto-calibration (Hilbert + Welch + dip search)
│   ├── analysis.py          Lorentz floor fit + β-separation integration
│   ├── scope.py             AcquireWorker (QThread) around SDS7404 driver
│   ├── processor.py         CoshXcorr wrapper + ProcessWorker + CalibrateWorker
│   ├── plot_widget.py       matplotlib FigureCanvas + analysis overlays
│   ├── settings_panel.py    Sidebar: data / optical / segments / display / analysis
│   └── main_window.py       Wires sidebar ↔ plot ↔ workers
├── vendor/                  Vendored third-party dependencies (see vendor/README.md)
│   ├── pycosh/              MIT, Maodong Gao 2022 — COSH reference implementation
│   └── sds7404/             Siglent SDS7404A LAN driver
└── examples/
    ├── sample_data.csv      ~4 MB, 100k samples × 250 MSa/s × 3 columns
    ├── make_sample_data.py  Regenerate from a longer source CSV
    └── README.md            How to load + recommended BW_SEGMENT for this sample
```

---

## Setup

```bash
git clone <repo-url>
cd dbpd_analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

That's it — `pycosh` and the `sds7404` driver are bundled under `vendor/`,
so no extra paths or external repos to clone. The app adds `vendor/` to
`sys.path` at import time.

### Power-user overrides

If you want to point at an external development checkout of either
dependency (e.g. to track upstream pycosh), set:

```bash
export DBPD_PYCOSH_PARENT=/path/to/folder-containing-pycosh
export DBPD_SDS7404_PARENT=/path/to/folder-containing-sds7404
```

The override takes effect after the bundled `vendor/` copy fails to import,
not before, so a clean clone always works out of the box.

## Run

```bash
.venv/bin/python main.py            # macOS / Linux
.venv\Scripts\python.exe main.py    # Windows
```

## Try the sample dataset

```bash
# After running the GUI:
# 1. sidebar mode → "Single CSV (3 columns: t, BPD1, BPD2)"
# 2. Browse → examples/sample_data.csv
# 3. Segments → change Bandwidth bins to:  10, 30, 100, 300, 1000, 3000, 10000
#    (the bundled sample is only 400 µs long, so the default 1-kHz bin
#     won't fit — see examples/README.md)
# 4. ▶ Process
```

---

## Workflow

### Mode A — Analyze existing CSV

1. Sidebar → **Data** → pick `Single CSV` or `Two CSVs` → Browse
2. **Optical path** → set delay length / n_core / AOM carrier (or click *Auto-calibrate FSR* once data is loaded)
3. **Segments** → leave defaults, or use the recommendation tables below
4. Processing runs **automatically** once FSR calibration succeeds; after changing optical or segment settings, click ▶ **Process** to re-run
5. **Analysis** card shows Lorentz FWHM and β-integrated Gaussian FWHM in real time; tweak the fit/integration bands to refine
6. **Export spectra…** writes one CSV containing both S<sub>ν</sub> and S<sub>φ</sub> (all traces + error columns) with a full metadata header

### Mode B — Live capture from oscilloscope

1. Sidebar → **Data** → mode `Acquire from oscilloscope (SDS7404)`
2. Set scope IP, BPD1 channel (default **C2**), BPD2 channel (default **C4**)
3. ✓ *Send SINGle trigger before reading* if you want a fresh acquisition; uncheck to read whatever frame is on screen
4. ⏺ **Acquire from scope** → background QThread pulls the frame, no UI freeze
5. (Optional) **Save acquired CSV…** to keep the raw record
6. Proceed from step 3 of Mode A

---

## How to read the spectrum

- **Cross-correlation curve (blue)** is the laser noise estimate with both BPDs' independent electronic noise suppressed
- **Lorentz floor (red dashed)** is the white-frequency-noise asymptote → FWHM<sub>L</sub> = π · S<sub>0</sub>
- **β-line (orange dotted)**: S<sub>ν</sub> = 8 ln(2) / π² · f ≈ 0.5615 · f. Above this line the noise contributes to the Gaussian (slow) line broadening; below, to the Lorentzian (fast) part. The integrated area gives FWHM<sub>G</sub> = √(8 ln 2 · A) (Di Domenico 2010)
- **Sharp peaks at n · FSR** at high offsets are G(f) compensation artifacts when the actual FSR matches the configured one — they should map onto the Lorentz floor when calibration is correct; visible peaks usually mean your fiber length is off → click *Auto-calibrate FSR*

---

## Measurement recommendations

These are the configurations we converged on for an 80 MHz AOM
self-heterodyne setup. Adapt the numbers if your AOM carrier differs.

### A — Choosing the sample rate

The Hilbert-based phase extraction loses validity when the analyzed
offset frequency approaches the AOM carrier f<sub>c</sub>. The safe
analysis ceiling is roughly **f<sub>c</sub> / 2** (sidebands stay
unambiguous); above ~f<sub>c</sub> the Hilbert filter folds the lower
sideband and the result is meaningless.

```
sample_rate ≥ 2 · f_c          (Nyquist absolute minimum)
sample_rate ≥ 2.5 · f_c        (recommended, leaves anti-alias margin)
```

For **80 MHz AOM**: minimum **200 MSa/s**, comfortable **250 MSa/s**.
Higher sample rates only waste memory.

### B — Choosing BW_SEGMENT

The list `BW_SEGMENT = [bw₀, bw₁, …]` (in Hz) defines a multi-band
Welch-style PSD estimate. Each band uses segment length
`1 / (bw · dt)`. Constraints:

| Quantity | Constraint | Why |
|---|---|---|
| `bw[0]` | ≥ 5 / T | Need ≥ 5 segments at the lowest band for tolerable 1σ jitter (~45%) |
| `bw[0] · ratio` | = f<sub>min</sub> (lowest analyzed offset) | Default `offset_start_ratio = 10` |
| ratio between bands | 3× (smooth) or 10× (compact) | 3× gives ~1.7× SNR jumps at band boundaries; 10× gives ~3.2× |
| `bw[-1]` | > 2/τ (= 2 · FSR) | Last band enters the incoherent limit so G(f) doesn't amplify single tones |
| `bw[-1] · ratio` | < f<sub>c</sub> / 2 | Don't analyze past Hilbert's valid range |

### C — Decision table for 80 MHz AOM, 7-10 m fiber

| Target | sample_rate | T | BW_SEGMENT (Hz) | offset range | Notes |
|---|---:|---:|---|---:|---|
| **Default** | 1 GSa/s | 5-10 ms | `1k, 3k, 10k, 30k, 100k, 300k, 1M, 3M, 10M` | 10 kHz – 30 MHz | Current working config |
| Compact / fast | 2 GSa/s | 1-5 ms | `10k, 30k, 100k, 300k, 1M, 3M, 10M, 30M` | 100 kHz – 30 MHz | Trades low-freq for record speed |
| Mid-band | 500 MSa/s | 0.5 s | `10, 30, 100, …, 10M` (12 bands) | 100 Hz – 30 MHz | Light acoustic isolation helpful |
| **Low-freq** | **200-250 MSa/s** | **5-10 s** | `1, 3, 10, 30, 100, 300, 1k, …, 1e7` (14 bands) | **10 Hz – 30 MHz** | **Needs ≥ 1 GSa scope memory + 100 m fiber + acoustic isolation** |
| Ultra-low-freq | 100-200 MSa/s | 60 s | `0.1, 0.3, 1, …, 1e6` | 1 Hz – 10 MHz | Multi-capture averaging required (single record will not fit in scope memory) |

### D — Memory math

`record_time = memory_depth_per_channel / sample_rate`

| Sample rate | 500 MSa scope | 1 GSa scope (option) | 2 GSa scope (option) |
|---:|---:|---:|---:|
| 500 MSa/s | 1.0 s | 2.0 s | 4.0 s |
| 250 MSa/s | 2.0 s | 4.0 s | 8.0 s |
| 200 MSa/s | 2.5 s | 5.0 s | 10.0 s |
| 100 MSa/s | 5.0 s | 10.0 s | 20.0 s |

For an 80 MHz AOM, **200 MSa/s is the practical floor** (below that
the AOM carrier approaches the Nyquist edge), so the achievable T is
fundamentally bounded by per-channel memory.

### E — Lowest reachable offset by hardware

Using `bw[0] · T ≥ 5` and `sample_rate ≥ 200 MSa/s`:

| Memory | Max T (@ 200 MSa/s) | Lowest f_min (single-shot) |
|---:|---:|---:|
| 500 MSa | 2.5 s | ≈ 20 Hz |
| 1 GSa | 5 s | ≈ 10 Hz |
| 2 GSa | 10 s | ≈ 5 Hz |

To reach below this — **use multi-capture averaging** (see below).

---

## Hardware checklist for low-frequency measurements

These matter more than software tuning once you go below ~1 kHz offset:

- **Delay fiber length** — short fiber (5-10 m) is fine above 10 kHz, but
  for low-frequency offsets you need longer τ so G(f) doesn't crush the
  signal below the BPD noise floor:
  - 7-10 m: useful above 10 kHz offset
  - 100 m: useful down to ~100 Hz offset (factor ~100 better low-freq sensitivity)
  - 1 km: useful to ~1 Hz offset (Yuan 2022 used 1 km)
- **Acoustic isolation** — long fiber picks up environmental vibration,
  which masquerades as real frequency noise:
  - Coil fiber inside a foam/sorbothane-lined box
  - Place on a vibration-isolated optical table
  - Kill HVAC / fans / pumps during measurement
- **Thermal isolation** — temperature drift directly modulates fiber
  delay; double-walled enclosure helps for very long records (> 10 s)
- **AOM drive cleanness** — 80 MHz AOM driver harmonics or sidebands
  show up directly in the spectrum; use a clean RF source

---

## Future feature — multi-capture averaging

When the target offset goes below what one scope record can give
(< ~10 Hz on 1 GSa memory), the planned solution is to capture
**N independent records back-to-back** and average their cross-PSDs:

```
Per-capture stats:    bw₀ · T → M segments,  1σ = 1/√M
After N captures:     N · M segments,         1σ = 1/√(N · M)
```

So 10 captures of 2 s at bw = 1 Hz gives 20 segments — same noise floor
as a single 20-second record but **without** needing a 4 GSa scope.

### What it will look like

Sidebar Acquire panel gets an additional **Average N captures** spin box
(default 1). When N > 1, the worker loops:

```
for i in 1..N:
    scope.single()              # fresh trigger
    frame_i = scope.read_channels(...)
    result_i = pycosh.process(frame_i)
    psd12_accum += result_i.psd12 / N
    progress(i / N)
```

Total wall-clock time ≈ N · (T + transfer_overhead). At 1 GSa/s
record and Gb-LAN, transfer is roughly 10-20 s per pull, so 10 captures
≈ 5-10 minutes; 100 captures ≈ 30-60 minutes.

### Not yet implemented

Single-capture analysis covers everything ≥ 10 Hz offset on 1 GSa memory,
which is fine for current work. Add it later when the project
genuinely needs ≤ 1 Hz offset measurements.

---

## Algorithm notes (pycosh)

For the underlying maths — Hilbert phase extraction, multi-band Welch
PSD, G(f) sinc² compensation (paper Eq. 22), and cross-correlation
suppression of BPD noise (paper Eq. 27-28) — see `pycosh/CoshXcorr.py`
and the Yuan 2022 paper. Two non-obvious points:

- The AOM carrier frequency is **not** a parameter; pycosh implicitly
  rejects it by skipping the DC bin (offsets start at
  `offset_start_ratio · bw[0]`)
- The app displays **single-sideband (SSB)** spectral densities: pycosh
  emits a two-sided PSD, which the app multiplies by 2 for display. In this
  convention Lorentz FWHM = π · S<sub>0</sub> and the β-line is 8 ln2/π² · f
  (Di Domenico 2010, one-sided)

The algorithm itself was numerically verified against simulated white-
frequency-noise lasers — see [verify_pycosh.py](../sds7404/verify_pycosh.py)
in the sister repo for the test.

---

## License

MIT — see [LICENSE](LICENSE).

Bundled vendored components keep their own licensing:
- `vendor/pycosh/`  MIT, Copyright (c) 2022 Maodong Gao
- `vendor/sds7404/`  MIT (this project)
