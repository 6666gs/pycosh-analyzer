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
    assert any(isinstance(c, str) and c.startswith("run(") for c in scope.calls)
    assert scope.calls.index(("read_channels", ("C1", "C2"))) < \
        [i for i, c in enumerate(scope.calls) if isinstance(c, str) and c.startswith("run(")][0]
    assert len(payloads) == 1  # finished_ok emitted


def test_acquire_worker_resume_false_skips_run(qtbot):
    from app.scope import AcquireWorker

    factory = FakeScopeFactory()
    worker = AcquireWorker("1.2.3.4", "C1", "C2", send_single=False,
                           resume=False, scope_factory=factory)
    worker.run()

    assert not any(isinstance(c, str) and c.startswith("run(") for c in factory.last.calls)


def test_acquire_worker_stops_before_read_when_not_single(qtbot):
    from app.scope import AcquireWorker

    factory = FakeScopeFactory()
    worker = AcquireWorker("1.2.3.4", "C1", "C2", send_single=False,
                           scope_factory=factory)
    worker.run()

    calls = factory.last.calls
    assert "single" not in calls                      # no blocking trigger wait
    # frame is frozen (stop) before the multi-channel read for coherence
    assert calls.index("stop") < calls.index(("read_channels", ("C1", "C2")))


def test_connection_worker_emits_idn_on_success(qtbot):
    from app.scope import TestConnectionWorker

    factory = FakeScopeFactory()
    worker = TestConnectionWorker("1.2.3.4", scope_factory=factory)
    idns = []
    worker.finished_ok.connect(lambda s: idns.append(s))

    worker.run()

    assert idns == ["FAKE,SDS7404,0,0"]
    # Connection opened and closed with no acquisition side effects.
    assert factory.last.calls == ["enter", "close"]


def test_connection_worker_emits_error_on_failure(qtbot):
    from app.scope import TestConnectionWorker

    def boom(host, **kwargs):
        raise OSError("host unreachable")

    worker = TestConnectionWorker("1.2.3.4", scope_factory=boom)
    errs = []
    worker.finished_err.connect(lambda m: errs.append(m))

    worker.run()

    assert errs and "host unreachable" in errs[0]


def test_average_acquire_worker_reads_n_times_and_averages(qtbot):
    import numpy as np

    from app.scope import AverageAcquireWorker

    factory = FakeScopeFactory()
    worker = AverageAcquireWorker(
        "1.2.3.4", "C1", n_avg=3, fsr_hz=1e6, n_skip=10, fmax=4e5,
        with_convergence=True, scope_factory=factory)
    results = []
    worker.finished_ok.connect(lambda r: results.append(r))

    worker.run()

    assert results
    final, snapshots = results[0]
    assert final.n_avg == 3
    assert np.isfinite(final.floor_hz2_per_hz)
    assert len(snapshots) == 3                  # checkpoints [1, 2, 3]
    # Free-running: never arms a blocking SINGle trigger; coherent stop→read→run.
    calls = factory.last.calls
    assert "single" not in calls
    assert "stop" in calls


def test_average_acquire_worker_auto_calibrates_fsr_when_none(qtbot, monkeypatch):
    """fsr_hz=None → the worker auto-calibrates from the first record only, then
    averages with that FSR."""
    from types import SimpleNamespace

    from app.scope import AverageAcquireWorker

    calls = []

    def fake_calibrate(v, sr, n_core, **kw):
        calls.append((v.size, sr, n_core))
        return SimpleNamespace(fsr_hz=2.0e6, delta_L_m=68.0)

    monkeypatch.setattr("app.mzi_calibrate.calibrate_mzi", fake_calibrate)

    factory = FakeScopeFactory()
    worker = AverageAcquireWorker(
        "1.2.3.4", "C1", n_avg=3, fsr_hz=None, n_skip=10, fmax=4e5,
        n_core=1.5, scope_factory=factory)
    results = []
    worker.finished_ok.connect(lambda r: results.append(r))

    worker.run()

    assert results
    final, _snapshots = results[0]
    assert final.fsr_hz == 2.0e6                 # used the auto-calibrated FSR
    assert worker.fsr_hz == 2.0e6               # resolved value exposed
    assert len(calls) == 1                       # calibrated from the FIRST record only
    assert calls[0][2] == 1.5                    # passed n_core through


def test_average_acquire_worker_errors_when_auto_calibration_fails(qtbot, monkeypatch):
    """fsr_hz=None and no detectable FSR dip → a clear error asking for Manual FSR."""
    from types import SimpleNamespace

    from app.scope import AverageAcquireWorker

    monkeypatch.setattr(
        "app.mzi_calibrate.calibrate_mzi",
        lambda v, sr, n_core, **kw: SimpleNamespace(fsr_hz=None, delta_L_m=None))

    factory = FakeScopeFactory()
    worker = AverageAcquireWorker(
        "1.2.3.4", "C1", n_avg=3, fsr_hz=None, n_skip=10, fmax=4e5,
        scope_factory=factory)
    oks, errs = [], []
    worker.finished_ok.connect(lambda r: oks.append(r))
    worker.finished_err.connect(lambda m: errs.append(m))

    worker.run()

    assert not oks
    assert errs and "Manual FSR" in errs[0]


def test_average_acquire_worker_keep_raw_retains_records(qtbot):
    """keep_raw=True keeps the N acquired traces + their sample rate so they can
    be saved as a multi-record file; default keep_raw=False keeps nothing."""
    import numpy as np

    from app.scope import AverageAcquireWorker

    factory = FakeScopeFactory()
    worker = AverageAcquireWorker(
        "1.2.3.4", "C1", n_avg=3, fsr_hz=1e6, n_skip=10, fmax=4e5,
        keep_raw=True, scope_factory=factory)
    worker.run()

    assert worker.raw_records is not None
    assert len(worker.raw_records) == 3
    assert all(isinstance(r, np.ndarray) for r in worker.raw_records)
    assert worker.sample_rate_hz is not None

    # Default: nothing retained.
    plain = AverageAcquireWorker(
        "1.2.3.4", "C1", n_avg=2, fsr_hz=1e6, n_skip=10, fmax=4e5,
        scope_factory=FakeScopeFactory())
    plain.run()
    assert plain.raw_records is None


def test_average_file_worker_matches_average_records(qtbot, tmp_path):
    """AverageFileWorker streams a multi-record file through the same
    PsdAverager pipeline → identical result to a direct average_records call."""
    import numpy as np

    from app.averaging import average_records
    from app.data_io import save_records
    from app.scope import AverageFileWorker

    rng = np.random.default_rng(3)
    sr = 1e6

    def rec():
        t = np.arange(4096) / sr
        return np.sin(2 * np.pi * 1e5 * t + 0.01 * np.cumsum(rng.standard_normal(4096)))

    records = np.stack([rec() for _ in range(4)])
    path = tmp_path / "multi.npz"
    save_records(path, records, sr)

    ref = average_records([records[i] for i in range(4)], sr, 5e5,
                          n_skip=10, fmax=4e5)

    worker = AverageFileWorker(str(path), fsr_hz=5e5, n_skip=10, fmax=4e5)
    results = []
    worker.finished_ok.connect(lambda r: results.append(r))
    worker.run()

    assert results
    final, _snapshots = results[0]
    assert final.n_avg == 4
    np.testing.assert_allclose(final.s_nu, ref.s_nu, rtol=1e-9)
    np.testing.assert_allclose(final.linewidth_hz, ref.linewidth_hz, rtol=1e-9)


def test_average_file_worker_auto_calibrates_when_fsr_none(qtbot, tmp_path, monkeypatch):
    """fsr_hz=None → auto-calibrate from the first record in the file (once)."""
    from types import SimpleNamespace

    import numpy as np

    from app.data_io import save_records
    from app.scope import AverageFileWorker

    calls = []
    monkeypatch.setattr(
        "app.mzi_calibrate.calibrate_mzi",
        lambda v, sr, n_core, **kw: (calls.append(1),
                                     SimpleNamespace(fsr_hz=2e6, delta_L_m=68.0))[1])

    sr = 1e6
    records = np.random.default_rng(0).standard_normal((3, 4096))
    path = tmp_path / "m.npz"
    save_records(path, records, sr)

    worker = AverageFileWorker(str(path), fsr_hz=None, n_skip=10, fmax=4e5,
                               n_core=1.5)
    results = []
    worker.finished_ok.connect(lambda r: results.append(r))
    worker.run()

    assert results
    final, _ = results[0]
    assert final.fsr_hz == 2e6
    assert len(calls) == 1          # calibrated from the FIRST record only
