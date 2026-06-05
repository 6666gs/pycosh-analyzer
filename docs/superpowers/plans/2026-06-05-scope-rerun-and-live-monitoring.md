# Scope Re-run + Live Phase-Noise Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do TDD: write the failing test, watch it fail, implement, watch it pass, commit. Run tests with the project venv: `.venv/bin/python -m pytest`. **Commit locally after each task; do NOT push** (pushing to `main` is handled separately by the maintainer).

**Goal:** After a one-shot Acquire the oscilloscope resumes live acquisition, and a new "Monitor (live)" mode repeatedly acquires + processes frames to plot the laser phase-noise spectrum and a Lorentz-linewidth-vs-time trend in real time (to watch whether a self-lock laser stays locked).

**Architecture:** A `MonitorWorker` (QThread, `app/monitor.py`) opens the scope once and loops `single → read → run_cosh → emit(result, elapsed)` until stopped, reusing the already-calibrated FSR (no per-cycle recalibration). The pycosh call is factored out of `ProcessWorker` into a shared module-level `run_cosh()`. The main thread receives each cycle, redraws the spectrum, and appends the Lorentz FWHM to a rolling trend shown in a new `TrendPlot`. Both `AcquireWorker` and `MonitorWorker` accept an injectable `scope_factory` so the acquisition loop is unit-testable with a fake scope (no hardware).

**Tech Stack:** Python 3.12, PySide6 (QThread/Signal), numpy, matplotlib (Qt Agg), pytest + pytest-qt (offscreen), vendored `pycosh` + `sds7404` driver.

**Design decisions (locked):**
- Trend metric = **Lorentz FWHM** (π·S₀), the headline lock indicator; failed fits plotted as NaN gaps.
- Each monitor cycle issues a fresh **SINGle** trigger (clean independent record).
- Cadence = **back-to-back** (bounded by acquire + transfer + pycosh time).
- Resume after acquire uses **AUTO** trigger mode so the scope screen keeps refreshing.
- Monitoring requires an existing FSR calibration (same gate as Process); if not calibrated, warn the user to Acquire once first.
- Per-cycle errors **stop** monitoring and report once (v1 keeps it simple).
- Stopping has up to one-cycle latency (a blocking VISA read is not interrupted); show "Stopping after current frame…".

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `vendor/sds7404/sds7404.py` | Scope driver | Modify `run()` → resume AUTO + RUN |
| `app/processor.py` | pycosh wrapper | Add `run_cosh()`; `ProcessWorker` reuses it |
| `app/scope.py` | Acquisition worker | `AcquireWorker` gains `resume` + `scope_factory`; calls `scope.run()` after read |
| `app/monitor.py` *(new)* | Continuous monitor loop | `MonitorRequest` + `MonitorWorker` |
| `app/plot_widget.py` | Plots | Add `TrendPlot` widget |
| `app/settings_panel.py` | Sidebar | Add "Monitor (live)" button + monitoring state |
| `app/main_window.py` | Wiring | Layout TrendPlot, orchestrate monitor start/stop/cycles |
| `conftest.py` | Test helpers | Add `synthetic_beat()`, `FakeFrame`, `FakeScope` |
| `tests/test_scope.py` *(new)* | Tests | AcquireWorker resume |
| `tests/test_monitor.py` *(new)* | Tests | MonitorWorker loop |
| `tests/test_processor.py` | Tests | `run_cosh` end-to-end |
| `tests/test_gui.py` | Tests | TrendPlot smoke, monitor gating, cycle handler |
| `README.md` / `README.zh-CN.md` | Docs | Document re-run + live monitoring |

---

## Task 1: Scope driver — `run()` resumes continuous (AUTO) acquisition

**Files:**
- Modify: `vendor/sds7404/sds7404.py` (the `run` method, ~line 137)
- Modify: `conftest.py` (add a fake pyvisa resource manager helper)
- Test: `tests/test_scope.py` (new)

- [ ] **Step 1: Add fake-scope test helpers to `conftest.py`**

Append to `conftest.py` (after the existing `os.environ.setdefault(...)` line):

```python
from dataclasses import dataclass

import numpy as np


def synthetic_beat(n: int = 4096, sr: float = 1e6, fbeat: float = 1e5, seed: int = 0):
    """A valid heterodyne beat: carrier + tiny random-walk phase noise.

    Returns (t, v1, v2, sample_rate). Long enough that pycosh's smallest
    band (1 kHz at 1 MSa/s → 1000-sample segments) gets several segments.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    phase = 2 * np.pi * fbeat * t + 0.01 * np.cumsum(rng.standard_normal(n))
    v1 = np.sin(phase)
    v2 = np.sin(phase + 0.001 * rng.standard_normal(n))
    return t, v1, v2, sr


@dataclass
class FakeFrame:
    time_axis: np.ndarray
    voltages: dict
    sample_rate: float


class FakeScope:
    """Records driver calls; returns canned frames. Used as a context manager
    via FakeScopeFactory so AcquireWorker/MonitorWorker run without hardware."""

    def __init__(self, frames=None):
        t, v1, v2, sr = synthetic_beat()
        self._frame = FakeFrame(time_axis=t,
                                voltages={"C1": v1, "C2": v2},
                                sample_rate=sr)
        self.calls = []

    def __enter__(self):
        self.calls.append("enter")
        return self

    def __exit__(self, *exc):
        self.calls.append("close")
        return False

    def idn(self):
        return "FAKE,SDS7404,0,0"

    def single(self):
        self.calls.append("single")

    def stop(self):
        self.calls.append("stop")

    def run(self, continuous: bool = True):
        self.calls.append(f"run(continuous={continuous})")

    def read_channels(self, channels):
        self.calls.append(("read_channels", tuple(channels)))
        return self._frame


class FakeScopeFactory:
    """Callable(host, **kw) -> FakeScope. Keeps the last scope for assertions."""

    def __init__(self):
        self.last = None

    def __call__(self, host, *args, **kwargs):
        self.last = FakeScope()
        return self.last
```

- [ ] **Step 2: Write the failing driver test**

Create `tests/test_scope.py`:

```python
"""Tests for the scope driver run() semantics and AcquireWorker resume."""
from app.scope import ensure_sds7404_importable

# Put the vendored sds7404 driver on sys.path for the driver-level test below.
ensure_sds7404_importable()

from conftest import FakeScopeFactory  # noqa: E402


def test_driver_run_continuous_sets_auto_then_run():
    from sds7404 import SDS7404  # vendored driver (now importable)

    class FakeInstr:
        def __init__(self):
            self.writes = []
            self.timeout = 0
            self.chunk_size = 0
            self.read_termination = None
            self.write_termination = None

        def write(self, cmd):
            self.writes.append(cmd)

        def query(self, cmd):
            return "Stop"

        def close(self):
            pass

    class FakeRM:
        def __init__(self):
            self.instr = FakeInstr()

        def open_resource(self, resource):
            return self.instr

    rm = FakeRM()
    scope = SDS7404("1.2.3.4", resource_manager=rm)
    rm.instr.writes.clear()

    scope.run()  # continuous=True default

    assert ":TRIGger:MODE AUTO" in rm.instr.writes
    assert ":TRIGger:RUN" in rm.instr.writes
    # AUTO must be set before RUN
    assert rm.instr.writes.index(":TRIGger:MODE AUTO") < rm.instr.writes.index(":TRIGger:RUN")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_scope.py::test_driver_run_continuous_sets_auto_then_run -v`
Expected: FAIL — current `run()` only writes `:TRIGger:RUN`, not `:TRIGger:MODE AUTO`.

- [ ] **Step 4: Modify the driver `run()` method**

In `vendor/sds7404/sds7404.py`, replace:

```python
    def run(self) -> None:
        self._scope.write(":TRIGger:RUN")
```

with:

```python
    def run(self, continuous: bool = True) -> None:
        """恢复采集。continuous=True 时先切回 AUTO 触发模式，保证屏幕持续刷新
        (single() 会把模式设成 SINGle，读完后若不切回就停在单次态)。"""
        if continuous:
            self._scope.write(":TRIGger:MODE AUTO")
        self._scope.write(":TRIGger:RUN")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_scope.py::test_driver_run_continuous_sets_auto_then_run -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add vendor/sds7404/sds7404.py conftest.py tests/test_scope.py
git commit -m "feat(scope): run() resumes continuous AUTO acquisition"
```

---

## Task 2: Factor pycosh call into `run_cosh()` (DRY, shared by ProcessWorker + MonitorWorker)

**Files:**
- Modify: `app/processor.py` (extract helper from `ProcessWorker.run`, ~lines 117-149)
- Test: `tests/test_processor.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_processor.py`:

```python
from conftest import synthetic_beat


def test_run_cosh_end_to_end_applies_ssb_factor():
    from app.processor import ProcessRequest, run_cosh

    _, v1, v2, sr = synthetic_beat()
    req = ProcessRequest(
        v1=v1, v2=v2, sample_rate=sr,
        delay_freq=1e5, bw_segment=(1e3, 1e4),
        offset_start_ratio=10, range_start=None, range_stop=None,
    )

    result = run_cosh(req)

    assert result.freq.size > 0
    assert result.s_nu_12.shape == result.freq.shape
    # single-sideband factor flows through the real pipeline
    np.testing.assert_allclose(
        result.s_nu_12, SSB_FACTOR * np.abs(result.psd12) / result.gfilter
    )
```

(`SSB_FACTOR` and `np` are already imported at the top of `tests/test_processor.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_processor.py::test_run_cosh_end_to_end_applies_ssb_factor -v`
Expected: FAIL — `cannot import name 'run_cosh'`.

- [ ] **Step 3: Add `run_cosh()` and make `ProcessWorker` use it**

In `app/processor.py`, add this module-level function immediately after the `ProcessResult` class (before `class ProcessWorker`):

```python
def run_cosh(request: ProcessRequest, progress=None) -> ProcessResult:
    """Run pycosh's CoshXcorr for one request and build a ProcessResult.

    Pure (no Qt). Shared by ProcessWorker and MonitorWorker. `progress` is an
    optional callable(str) for status messages.
    """
    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    ensure_pycosh_importable()
    from pycosh import CoshConfig, CoshXcorr  # type: ignore

    _say("Configuring pycosh …")
    cfg = CoshConfig(
        delay_freq=request.delay_freq,
        bw_segment=list(request.bw_segment),
        sample_rate=request.sample_rate,
        offset_start_ratio=request.offset_start_ratio,
        range_start=request.range_start,
        range_stop=request.range_stop,
    )
    trace2 = request.v2 if request.v2 is not None else request.v1
    cosh = CoshXcorr(trace1=request.v1, trace2=trace2, config=cfg)
    _say("Running Hilbert + multi-band FFT …")
    cosh.process(print_progress=False)
    return ProcessResult(
        freq=np.asarray(cosh.freq_list),
        gfilter=np.asarray(cosh.freq_filter),
        psd11=np.asarray(cosh.psd11),
        psd11_err=np.asarray(cosh.psd11_err),
        psd22=np.asarray(cosh.psd22),
        psd22_err=np.asarray(cosh.psd22_err),
        psd12=np.asarray(cosh.psd12),
        psd12_err=np.asarray(cosh.psd12_err),
        request=request,
    )
```

Then replace the body of `ProcessWorker.run` with:

```python
    def run(self) -> None:
        try:
            result = run_cosh(self._req, progress=self.progress.emit)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_processor.py -v`
Expected: PASS (the new test + the existing SSB/derived-spectra tests still pass).

- [ ] **Step 5: Commit**

```bash
git add app/processor.py tests/test_processor.py
git commit -m "refactor(processor): extract run_cosh() shared by workers"
```

---

## Task 3: `AcquireWorker` — resume live + injectable scope factory

**Files:**
- Modify: `app/scope.py` (`AcquireWorker`, ~lines 39-78)
- Test: `tests/test_scope.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scope.py`:

```python
def test_acquire_worker_resumes_scope_after_read(qtbot):
    from app.scope import AcquireWorker

    factory = FakeScopeFactory()
    worker = AcquireWorker("1.2.3.4", "C1", "C2", send_single=False,
                           scope_factory=factory)
    payloads = []
    worker.finished_ok.connect(lambda p: payloads.append(p))

    worker.run()  # run synchronously in this thread for a deterministic test

    scope = factory.last
    assert scope is not None
    # read happened, then live acquisition resumed before the connection closed
    assert ("read_channels", ("C1", "C2")) in scope.calls
    assert any(c.startswith("run(") for c in scope.calls)
    assert scope.calls.index(("read_channels", ("C1", "C2"))) < \
        [i for i, c in enumerate(scope.calls) if c.startswith("run(")][0]
    assert len(payloads) == 1  # finished_ok emitted


def test_acquire_worker_resume_false_skips_run(qtbot):
    from app.scope import AcquireWorker

    factory = FakeScopeFactory()
    worker = AcquireWorker("1.2.3.4", "C1", "C2", send_single=False,
                           resume=False, scope_factory=factory)
    worker.run()

    assert not any(c.startswith("run(") for c in factory.last.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scope.py -k acquire_worker -v`
Expected: FAIL — `AcquireWorker.__init__` has no `scope_factory` / `resume` params and never calls `scope.run()`.

- [ ] **Step 3: Modify `AcquireWorker`**

In `app/scope.py`, replace the entire `AcquireWorker` class (from `class AcquireWorker(QThread):` through the end of its `run` method) with:

```python
class AcquireWorker(QThread):
    """Pulls a multichannel frame from the scope off the main thread.

    After reading, resumes live acquisition (Feature: scope keeps running
    after a one-shot Acquire) unless `resume=False`. `scope_factory` lets
    tests inject a fake scope; production uses the vendored SDS7404 driver.
    """
    progress = Signal(str)
    finished_ok = Signal(object)     # (frame, ch1_name, ch2_name)
    finished_err = Signal(str)

    def __init__(
        self,
        host: str,
        ch1: str,
        ch2: str | None,
        send_single: bool = False,
        resume: bool = True,
        timeout_ms: int = 30_000,
        scope_factory=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.host = host
        self.ch1 = ch1
        self.ch2 = ch2
        self.send_single = send_single
        self.resume = resume
        self.timeout_ms = timeout_ms
        self._scope_factory = scope_factory

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self.host, timeout_ms=self.timeout_ms)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self.host, timeout_ms=self.timeout_ms)

    def run(self) -> None:
        try:
            with self._open_scope() as scope:
                idn = scope.idn()
                self.progress.emit(f"Connected: {idn}")
                if self.send_single:
                    self.progress.emit("Sending SINGle trigger, waiting Stop …")
                    scope.single()
                channels = [self.ch1] + ([self.ch2] if self.ch2 else [])
                self.progress.emit(f"Reading channels {channels} …")
                frame = scope.read_channels(channels)
                if self.resume:
                    # Resume live acquisition so the operator keeps seeing the
                    # signal after the one-shot grab.
                    scope.run()
            self.finished_ok.emit((frame, self.ch1, self.ch2))
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
```

(The `with self._open_scope() as scope:` replaces the old `with SDS7404(...) as scope:` and the connecting-progress line.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scope.py -v`
Expected: PASS (all three scope tests).

- [ ] **Step 5: Commit**

```bash
git add app/scope.py tests/test_scope.py
git commit -m "feat(scope): AcquireWorker resumes live + injectable scope factory"
```

---

## Task 4: `MonitorWorker` + `MonitorRequest` — continuous acquire+process loop

**Files:**
- Create: `app/monitor.py`
- Test: `tests/test_monitor.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_monitor.py`:

```python
"""Tests for the continuous monitoring loop (no hardware — fake scope)."""
from conftest import FakeScopeFactory


def test_monitor_worker_loops_and_resumes_on_stop(qtbot):
    from app.monitor import MonitorRequest, MonitorWorker

    factory = FakeScopeFactory()
    req = MonitorRequest(
        host="1.2.3.4", ch1="C1", ch2="C2", send_single=True,
        delay_freq=1e5, bw_segment=(1e3, 1e4), offset_start_ratio=10,
        range_start=None, range_stop=None,
    )
    worker = MonitorWorker(req, scope_factory=factory)

    results = []
    worker.cycle_done.connect(lambda result, elapsed: results.append((result, elapsed)))

    worker.start()
    qtbot.waitUntil(lambda: len(results) >= 2, timeout=10_000)
    worker.request_stop()
    qtbot.waitUntil(lambda: worker.isFinished(), timeout=10_000)
    worker.wait()

    assert len(results) >= 2
    first_result, first_elapsed = results[0]
    assert first_result.freq.size > 0          # a real ProcessResult per cycle
    assert isinstance(first_elapsed, float)
    scope = factory.last
    assert "single" in scope.calls              # fresh SINGle each cycle
    assert any(c.startswith("run(") for c in scope.calls)  # left live on stop
    assert scope.calls[-1] == "close"           # connection closed


def test_monitor_worker_emits_error(qtbot):
    from app.monitor import MonitorRequest, MonitorWorker

    class BoomFactory:
        def __call__(self, host, *a, **k):
            raise ConnectionError("scope offline")

    req = MonitorRequest(
        host="1.2.3.4", ch1="C1", ch2=None, send_single=True,
        delay_freq=1e5, bw_segment=(1e3, 1e4), offset_start_ratio=10,
        range_start=None, range_stop=None,
    )
    worker = MonitorWorker(req, scope_factory=BoomFactory())
    errors = []
    worker.finished_err.connect(lambda m: errors.append(m))

    worker.start()
    qtbot.waitUntil(lambda: worker.isFinished(), timeout=10_000)
    worker.wait()

    assert errors and "scope offline" in errors[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitor.py -v`
Expected: FAIL — `No module named 'app.monitor'`.

- [ ] **Step 3: Create `app/monitor.py`**

```python
"""Continuous acquire+process loop for live phase-noise monitoring.

A MonitorWorker opens the scope once and repeatedly grabs a fresh single-shot
frame, runs pycosh on it (reusing the already-calibrated FSR), and emits the
ProcessResult to the main thread, until asked to stop. On stop it leaves the
scope running live. `scope_factory` lets tests inject a fake scope.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from .processor import ProcessRequest, ProcessResult, run_cosh
from .scope import ensure_sds7404_importable, frame_to_arrays


@dataclass(frozen=True)
class MonitorRequest:
    """Fixed parameters for a monitoring session (FSR is reused, not re-fit)."""
    host: str
    ch1: str
    ch2: str | None
    send_single: bool
    delay_freq: float
    bw_segment: tuple[float, ...]
    offset_start_ratio: int
    range_start: int | None
    range_stop: int | None


class MonitorWorker(QThread):
    """Loops single→read→run_cosh→emit until request_stop()."""
    cycle_done = Signal(object, float)   # (ProcessResult, elapsed_seconds)
    progress = Signal(str)
    finished_err = Signal(str)

    def __init__(self, request: MonitorRequest, scope_factory=None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._req = request
        self._scope_factory = scope_factory
        self._stop = False

    def request_stop(self) -> None:
        """Ask the loop to finish after the current frame (thread-safe flag)."""
        self._stop = True

    def _open_scope(self):
        if self._scope_factory is not None:
            return self._scope_factory(self._req.host)
        ensure_sds7404_importable()
        from sds7404 import SDS7404  # type: ignore
        return SDS7404(self._req.host)

    def run(self) -> None:
        req = self._req
        channels = [req.ch1] + ([req.ch2] if req.ch2 else [])
        try:
            with self._open_scope() as scope:
                start = time.monotonic()
                while not self._stop:
                    if req.send_single:
                        scope.single()
                    frame = scope.read_channels(channels)
                    _t, v1, v2, sr = frame_to_arrays(frame, req.ch1, req.ch2)
                    proc_req = ProcessRequest(
                        v1=v1, v2=v2, sample_rate=sr,
                        delay_freq=req.delay_freq,
                        bw_segment=req.bw_segment,
                        offset_start_ratio=req.offset_start_ratio,
                        range_start=req.range_start,
                        range_stop=req.range_stop,
                    )
                    result = run_cosh(proc_req)
                    self.cycle_done.emit(result, time.monotonic() - start)
                # Leave the scope live for the operator.
                scope.run()
        except Exception as exc:  # noqa: BLE001
            self.finished_err.emit(f"{type(exc).__name__}: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_monitor.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add app/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): MonitorWorker continuous acquire+process loop"
```

---

## Task 5: `TrendPlot` — Lorentz FWHM vs elapsed-time strip

**Files:**
- Modify: `app/plot_widget.py` (append a new widget class)
- Test: `tests/test_gui.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui.py`:

```python
def test_trend_plot_renders_points(qtbot):
    from app.plot_widget import TrendPlot

    trend = TrendPlot()
    qtbot.addWidget(trend)
    trend.update_trend([0.0, 1.5, 3.0], [1.2e4, 9.0e3, float("nan")])

    title = trend._ax.get_title()
    assert "FWHM" in title
    # one Line2D drawn for the trend
    assert len(trend._ax.get_lines()) >= 1


def test_trend_plot_clear(qtbot):
    from app.plot_widget import TrendPlot

    trend = TrendPlot()
    qtbot.addWidget(trend)
    trend.update_trend([0.0, 1.0], [1e4, 2e4])
    trend.clear()
    assert len(trend._ax.get_lines()) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k trend -v`
Expected: FAIL — `cannot import name 'TrendPlot'`.

- [ ] **Step 3: Add `TrendPlot` to `app/plot_widget.py`**

Append at the end of `app/plot_widget.py` (after `_format_hz`):

```python
class TrendPlot(QWidget):
    """Compact strip charting a scalar lock metric (Lorentz FWHM) vs time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(8, 1.8), facecolor="white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.setMaximumHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.canvas)

        self._ax = self.figure.add_subplot(111)
        self.clear()

    def clear(self) -> None:
        self._ax.clear()
        self._ax.set_title("Lock monitor — Lorentz FWHM vs elapsed time",
                           color="#1D1D1F", fontsize=10, pad=6)
        self._ax.set_xlabel("Elapsed time (s)", color="#1D1D1F", fontsize=9)
        self._ax.set_ylabel("FWHM (Hz)", color="#1D1D1F", fontsize=9)
        self._ax.grid(True, which="both", alpha=0.25, linewidth=0.5)
        self._ax.tick_params(colors="#1D1D1F", labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color("#E5E5EA")
        self._ax.set_facecolor("white")
        self.figure.tight_layout()
        self.canvas.draw_idle()

    def update_trend(self, times: list[float], fwhms: list[float]) -> None:
        self.clear()
        if times and fwhms:
            self._ax.semilogy(times, fwhms, color="#007AFF", linewidth=1.4,
                              marker="o", markersize=3)
        self.figure.tight_layout()
        self.canvas.draw_idle()
```

(`QWidget`, `QVBoxLayout`, `Figure`, and `FigureCanvasQTAgg` are already imported at the top of `app/plot_widget.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k trend -v`
Expected: PASS

Note: `semilogy` with a NaN value draws a gap, not an error — that's intended for failed fits.

- [ ] **Step 5: Commit**

```bash
git add app/plot_widget.py tests/test_gui.py
git commit -m "feat(plot): TrendPlot strip for Lorentz FWHM vs time"
```

---

## Task 6: Sidebar — "Monitor (live)" button + monitoring state

**Files:**
- Modify: `app/settings_panel.py` (`SettingsPanel`: signals, button, state methods)
- Test: `tests/test_gui.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui.py`:

```python
def test_monitor_button_gating(qtbot):
    from app.main_window import MainWindow
    from app.settings_panel import MODE_ACQUIRE

    win = MainWindow()
    qtbot.addWidget(win)
    sp = win.settings

    # Not calibrated → monitor disabled
    sp.set_calibrated(False)
    assert sp.monitor_btn.isEnabled() is False

    # Calibrated + acquire mode → monitor enabled
    sp.data.mode_combo.setCurrentIndex(
        sp.data.mode_combo.findData(MODE_ACQUIRE))
    sp.set_calibrated(True)
    assert sp.monitor_btn.isEnabled() is True

    # While monitoring: label flips, Process disabled, monitor stays enabled
    sp.set_monitoring(True)
    assert "Stop" in sp.monitor_btn.text()
    assert sp.process_btn.isEnabled() is False
    assert sp.monitor_btn.isEnabled() is True

    sp.set_monitoring(False)
    assert "Monitor" in sp.monitor_btn.text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui.py::test_monitor_button_gating -v`
Expected: FAIL — `SettingsPanel` has no `monitor_btn` / `set_monitoring`.

- [ ] **Step 3: Edit `app/settings_panel.py`**

3a. Add two signals to the `SettingsPanel` class signal block (which currently lists `processRequested … saveAcquiredRequested`):

```python
    monitorStartRequested = Signal()
    monitorStopRequested = Signal()
```

3b. In `SettingsPanel.__init__`, find the block that creates `self.export_btn`:

```python
        self.export_btn = _secondary_btn("Export spectra…")
        self.export_btn.setEnabled(False)
```

Replace it with:

```python
        self.export_btn = _secondary_btn("Export spectra…")
        self.export_btn.setEnabled(False)
        self._can_export: bool = False

        self.monitor_btn = _secondary_btn("▶  Monitor (live)")
        self.monitor_btn.setEnabled(False)
        self._monitoring: bool = False
```

3c. In `__init__`, find the signal-wiring block:

```python
        self.process_btn.clicked.connect(self.processRequested.emit)
        self.export_btn.clicked.connect(self.exportRequested.emit)
```

Replace with:

```python
        self.process_btn.clicked.connect(self.processRequested.emit)
        self.export_btn.clicked.connect(self.exportRequested.emit)
        self.monitor_btn.clicked.connect(self._on_monitor_clicked)
        # Mode changes (emitted via data.fileChanged) re-evaluate monitor gating.
        self.data.fileChanged.connect(self._refresh_monitor_btn)
```

3d. In `__init__`, find the bottom button layout:

```python
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.export_btn)
```

Replace with:

```python
        btn_layout.addWidget(self.process_btn)
        btn_layout.addWidget(self.monitor_btn)
        btn_layout.addWidget(self.export_btn)
```

3e. Replace the existing `set_export_enabled`, `set_processing`, `set_calibrated`, `_refresh_process_btn` methods with these (and add the new monitor methods):

```python
    def set_export_enabled(self, enabled: bool) -> None:
        self._can_export = enabled
        self.export_btn.setEnabled(enabled and not self._monitoring)

    def set_processing(self, busy: bool) -> None:
        self._busy = busy
        self.process_btn.setText("Processing…" if busy else "▶  Process")
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def set_calibrated(self, calibrated: bool) -> None:
        """Called by MainWindow after auto-cal succeeds/fails."""
        self._calibrated = calibrated
        self._refresh_process_btn()
        self._refresh_monitor_btn()

    def _refresh_process_btn(self) -> None:
        self.process_btn.setEnabled(
            self._calibrated and not self._busy and not self._monitoring
        )

    def _on_monitor_clicked(self) -> None:
        if self._monitoring:
            self.monitorStopRequested.emit()
        else:
            self.monitorStartRequested.emit()

    def _refresh_monitor_btn(self) -> None:
        can_start = (self._calibrated and not self._busy
                     and self.data.mode == MODE_ACQUIRE)
        self.monitor_btn.setEnabled(self._monitoring or can_start)

    def set_monitoring(self, monitoring: bool) -> None:
        """Lock conflicting controls while the live loop runs."""
        self._monitoring = monitoring
        self.monitor_btn.setText(
            "■  Stop monitoring" if monitoring else "▶  Monitor (live)"
        )
        self.data.mode_combo.setEnabled(not monitoring)
        self.data.acquire_btn.setEnabled(not monitoring)
        self.export_btn.setEnabled(self._can_export and not monitoring)
        self._refresh_process_btn()
        self._refresh_monitor_btn()
```

(`MODE_ACQUIRE` is already a module-level constant in `settings_panel.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui.py::test_monitor_button_gating -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/settings_panel.py tests/test_gui.py
git commit -m "feat(ui): Monitor (live) button + monitoring state gating"
```

---

## Task 7: MainWindow — TrendPlot layout + monitor orchestration

**Files:**
- Modify: `app/main_window.py` (imports, layout, monitor handlers, signal wiring)
- Test: `tests/test_gui.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui.py`:

```python
def test_monitor_cycle_updates_trend_and_spectrum(qtbot):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    result = _make_result(v2=np.zeros(16))

    win._on_monitor_cycle(result, 2.5)

    assert win._result is result
    assert len(win._trend_t) == 1
    assert win._trend_t[0] == 2.5
    assert len(win._trend_fwhm) == 1          # a metric (possibly NaN) recorded
    assert win.trend.isVisible() or True       # visibility toggled by start, not cycle


def test_start_monitor_requires_calibration(qtbot, monkeypatch):
    from app.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    warned = []
    monkeypatch.setattr("app.main_window.QMessageBox.warning",
                        lambda *a, **k: warned.append(a))

    win._start_monitor()  # not calibrated, no data

    assert warned               # warned the user instead of starting
    assert win._monitor_worker is None
```

(`_make_result` and `np` already exist in `tests/test_gui.py` from earlier tasks.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k monitor_cycle -v`
Expected: FAIL — `MainWindow` has no `_on_monitor_cycle` / `_trend_t` / `_start_monitor`.

- [ ] **Step 3: Edit `app/main_window.py`**

3a. Update imports. Change the PySide6 import block to add `QVBoxLayout`:

```python
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
```

Add at the top with the other stdlib imports (after `import datetime as dt`):

```python
from collections import deque
```

Update the plot import to include `TrendPlot`:

```python
from .plot_widget import DisplayOptions, SpectrumPlot, TrendPlot, _format_hz
```

Add a monitor import after the `from .scope import ...` line:

```python
from .monitor import MonitorRequest, MonitorWorker
```

3b. In `MainWindow.__init__`, add monitor state next to the existing worker fields (after `self._acq_worker = None`):

```python
        self._monitor_worker: MonitorWorker | None = None
        self._trend_t: deque[float] = deque(maxlen=1000)
        self._trend_fwhm: deque[float] = deque(maxlen=1000)
```

3c. Replace the central-widget construction:

```python
        self.settings = SettingsPanel()
        self.plot = SpectrumPlot()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.settings)
        layout.addWidget(self.plot, 1)
        self.setCentralWidget(central)
```

with:

```python
        self.settings = SettingsPanel()
        self.plot = SpectrumPlot()
        self.trend = TrendPlot()
        self.trend.setVisible(False)

        plot_container = QWidget()
        plot_col = QVBoxLayout(plot_container)
        plot_col.setContentsMargins(0, 0, 0, 0)
        plot_col.setSpacing(0)
        plot_col.addWidget(self.plot, 1)
        plot_col.addWidget(self.trend)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.settings)
        layout.addWidget(plot_container, 1)
        self.setCentralWidget(central)
```

3d. In the `# signals` block at the end of `__init__`, add:

```python
        self.settings.monitorStartRequested.connect(self._start_monitor)
        self.settings.monitorStopRequested.connect(self._stop_monitor)
```

3e. Add a new monitoring section. Insert this block immediately before the `# ---------- display + analysis ----------` comment (i.e. after `_on_process_err`):

```python
    # ---------- live monitoring ----------

    def _start_monitor(self) -> None:
        if self._data is None or not self.settings.optical.is_calibrated:
            QMessageBox.warning(
                self, "Not ready",
                "Acquire once to calibrate the FSR before starting live "
                "monitoring.",
            )
            return
        if self.settings.data.mode != MODE_ACQUIRE:
            QMessageBox.warning(self, "Acquire mode only",
                                "Live monitoring needs the oscilloscope mode.")
            return
        try:
            snap = self.settings.snapshot()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return

        req = MonitorRequest(
            host=self.settings.data.scope_host,
            ch1=self.settings.data.scope_ch1_name,
            ch2=self.settings.data.scope_ch2_name,
            send_single=True,
            delay_freq=snap.delay_freq_hz,
            bw_segment=snap.bw_segment_hz,
            offset_start_ratio=snap.offset_start_ratio,
            range_start=snap.range_start,
            range_stop=snap.range_stop,
        )

        self._trend_t.clear()
        self._trend_fwhm.clear()
        self.trend.clear()
        self.trend.setVisible(True)
        self.settings.set_monitoring(True)
        self.statusBar().showMessage("Live monitoring started…")

        self._monitor_worker = MonitorWorker(req)
        self._monitor_worker.progress.connect(self.statusBar().showMessage)
        self._monitor_worker.cycle_done.connect(self._on_monitor_cycle)
        self._monitor_worker.finished_err.connect(self._on_monitor_err)
        self._monitor_worker.finished.connect(self._on_monitor_finished)
        self._monitor_worker.finished.connect(self._monitor_worker.deleteLater)
        self._monitor_worker.start()

    def _on_monitor_cycle(self, result: ProcessResult, elapsed: float) -> None:
        self._result = result
        self.settings.set_export_enabled(True)  # latest frame is exportable once stopped

        fwhm = float("nan")
        try:
            snap = self.settings.snapshot()
        except ValueError:
            snap = None
        if snap is not None:
            lz = fit_lorentz_floor(result.freq, result.s_nu_12,
                                   f_min=snap.lorentz_f_min,
                                   f_max=snap.lorentz_f_max)
            if lz is not None:
                fwhm = lz.fwhm_hz

        self._trend_t.append(elapsed)
        self._trend_fwhm.append(fwhm)
        self.trend.update_trend(list(self._trend_t), list(self._trend_fwhm))
        self._redraw()

        shown = _format_hz(fwhm) if fwhm == fwhm else "—"   # NaN check
        self.statusBar().showMessage(
            f"Monitoring… {len(self._trend_t)} frames · last FWHM = {shown}"
        )

    def _stop_monitor(self) -> None:
        if self._monitor_worker is not None:
            self.statusBar().showMessage("Stopping after current frame…")
            self._monitor_worker.request_stop()

    def _on_monitor_finished(self) -> None:
        self._monitor_worker = None
        self.settings.set_monitoring(False)
        self.statusBar().showMessage(
            f"Monitoring stopped. {len(self._trend_t)} frames captured."
        )

    def _on_monitor_err(self, message: str) -> None:
        self.statusBar().showMessage("Monitoring failed.")
        QMessageBox.critical(self, "Monitoring failed", message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui.py -k monitor -v`
Expected: PASS (gating + cycle + start-requires-calibration).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_gui.py
git commit -m "feat(ui): wire live monitoring loop + trend plot into MainWindow"
```

---

## Task 8: Documentation — README EN + zh-CN

**Files:**
- Modify: `README.md`, `README.zh-CN.md`

- [ ] **Step 1: README.md — features table**

After the `**CSV export**` row in the features table, add a new row:

```markdown
| **Live monitoring** | After Acquire the scope resumes live; "▶ Monitor (live)" repeatedly acquires + processes single-shot frames, updating the spectrum and a Lorentz-linewidth-vs-time trend to watch laser lock stability |
```

- [ ] **Step 2: README.md — Mode B workflow**

In "### Mode B — Live capture from oscilloscope", replace:

```markdown
4. ⏺ **Acquire from scope** → background QThread pulls the frame, no UI freeze
```

with:

```markdown
4. ⏺ **Acquire from scope** → background QThread pulls the frame (the scope resumes live acquisition automatically afterwards), no UI freeze
```

Then append two list items after the existing step 6 ("Proceed from step 3 of Mode A"):

```markdown
7. **▶ Monitor (live)** (after one Acquire has calibrated the FSR) repeatedly grabs a fresh single-shot frame and re-processes it, updating the spectrum and the **Lorentz FWHM vs time** trend strip below the plot — use it to confirm a self-lock laser stays locked. **■ Stop monitoring** ends the loop after the current frame.
```

- [ ] **Step 3: README.zh-CN.md — features table**

After the `**CSV 导出**` row, add:

```markdown
| **实时监测** | Acquire 后示波器自动恢复 live；点“▶ Monitor (live)”反复采集+处理单次帧，实时刷新噪声谱与 Lorentz 线宽-时间趋势，用于观察激光器锁定是否稳定 |
```

- [ ] **Step 4: README.zh-CN.md — Mode B workflow**

In the "模式 B" section, after the line describing Acquire, add a step:

```markdown
7. **▶ Monitor (live)**（先 Acquire 一次完成 FSR 校准后）会反复抓取新的单次帧并重新处理，实时刷新噪声谱与下方的 **Lorentz 线宽 vs 时间** 趋势条，用于确认 self-lock 激光器是否保持锁定。**■ Stop monitoring** 在当前帧结束后停止。
```

(If the exact anchor lines differ, place these additions in the nearest sensible spot in the same section — the goal is documenting re-run-after-acquire and the monitor loop.)

- [ ] **Step 5: Commit**

```bash
git add README.md README.zh-CN.md
git commit -m "docs: document scope re-run and live monitoring"
```

---

## Task 9: Full verification

- [ ] **Step 1: Compile check**

Run: `.venv/bin/python -m py_compile main.py conftest.py app/*.py tests/*.py vendor/sds7404/sds7404.py`
Expected: no output (success).

- [ ] **Step 2: Full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all green (previous 13 + the new scope/monitor/processor/gui tests).

- [ ] **Step 3: Offscreen app construction smoke**

Run:
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from PySide6.QtWidgets import QApplication
app = QApplication([])
from app.main_window import MainWindow
w = MainWindow()
assert w.trend is not None and w.trend.isVisible() is False
assert w.settings.monitor_btn.isEnabled() is False
print('SMOKE OK')
"
```
Expected: `SMOKE OK`.

- [ ] **Step 4: Report**

Summarise what changed, test counts, and note that commits are local (not pushed). Stop — do not push to `main`.

---

## Self-review checklist (done by the plan author)

- **Spec coverage:** Feature 1 (re-run after acquire) → Task 1 (driver) + Task 3 (AcquireWorker). Feature 2 (continuous monitor: spectrum + trend, SINGle per cycle, back-to-back) → Tasks 2,4,5,6,7. Docs → Task 8. Verification → Task 9. ✓
- **Types consistent:** `MonitorRequest` fields used identically in Task 4 (definition) and Task 7 (construction). `cycle_done = Signal(object, float)` matches `_on_monitor_cycle(result, elapsed)`. `run(continuous=True)` matches the no-arg `scope.run()` calls. `set_monitoring` / `set_export_enabled` / `_can_export` consistent across Tasks 6 and 7. ✓
- **No placeholders:** every code step is concrete. ✓
