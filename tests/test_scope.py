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
