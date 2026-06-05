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
