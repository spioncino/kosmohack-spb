from typing import Protocol

from evarisk.orbit.trajectory import Trajectory
from evarisk.store.asof import AsOf
from evarisk.types import LineAssessment, Window


class RiskLine(Protocol):
    name: str
    mechanism: str

    def assess(self, window: Window, trajectory: Trajectory, asof: AsOf) -> LineAssessment: ...
