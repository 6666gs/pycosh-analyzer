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
    assert any(isinstance(c, str) and c.startswith("run(") for c in scope.calls)  # left live on stop
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
