# examples/

A tiny sample dataset for getting `dbpd_analyzer` running without setting
up real hardware.

## `sample_data.csv`

- **Origin**: dual-BPD self-heterodyne measurement of a narrow-linewidth
  laser (80 MHz AOM, ~8 m delay fiber), original 2 GSa/s × 10 ms recording
- **Processing**: 8× decimated (anti-aliased IIR, zero-phase) to **250 MSa/s**;
  first 100,000 samples (= **400 µs**) kept; 6 significant-figure CSV
- **Format**: 3 columns `t_s, C2, C4` (time, BPD1 voltage, BPD2 voltage)
- **Size**: ~4 MB

400 µs is very short — much shorter than a real measurement record — so the
default sidebar BW_SEGMENT (which starts at `1 kHz`) cannot be used:
`bw = 1 kHz` needs 1 ms of data minimum, and `bw = 3 kHz` only just fits a
single 333 µs segment with no averaging.

### Recommended BW_SEGMENT for this sample

In the sidebar Segments card, override the default to:

```
10, 30, 100, 300, 1000, 3000, 10000          (in kHz)
```

with `offset_start_ratio = 10`. This gives:

| bw | segment length | # segments in 400 µs |
|---:|---:|---:|
| 10 kHz | 100 µs | 4 |
| 30 kHz | 33 µs | 12 |
| 100 kHz | 10 µs | 40 |
| 300 kHz | 3.3 µs | 120 |
| 1 MHz | 1.0 µs | 400 |
| 3 MHz | 333 ns | 1200 |

→ analyzed offset range **100 kHz – 30 MHz**, ~150 frequency points.

For real low-frequency work you'll want longer records — see the main
README's measurement recommendations table.

### How to regenerate

If `sample_data.csv` ever needs to be refreshed (different signal, different
trim window, etc.):

```bash
.venv/bin/python examples/make_sample_data.py
```

Adjust `SOURCE_CSV` / `DECIM_FACTOR` / `N_KEEP` at the top of that script.
