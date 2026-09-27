"""Ghost crew: one mini ghost per active background worker.

Cap four visible; overflow counts the rest. Completed workers dissolve
out (a few ticks of shrinking) instead of vanishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_CREW = 4
DISSOLVE_TICKS = 4


@dataclass
class CrewMember:
    worker_id: str
    activity: str  # reading | writing | building | thinking
    label: str
    dissolve_left: int = 0  # >0 while dissolving out

    @property
    def dissolving(self) -> bool:
        return self.dissolve_left > 0


@dataclass
class CrewState:
    members: list[CrewMember] = field(default_factory=list)

    def add(self, worker_id: str, activity: str, label: str) -> None:
        """A worker started: add it, or refresh it if already present."""
        for member in self.members:
            if member.worker_id == worker_id:
                member.activity = activity
                member.label = label
                member.dissolve_left = 0
                return
        self.members.append(CrewMember(worker_id, activity, label))

    def complete(self, worker_id: str) -> None:
        """A worker finished: start its dissolve-out."""
        for member in self.members:
            if member.worker_id == worker_id and not member.dissolving:
                member.dissolve_left = DISSOLVE_TICKS

    def tick(self) -> None:
        """Advance dissolve-outs; drop members when they finish."""
        kept = []
        for member in self.members:
            if member.dissolving:
                member.dissolve_left -= 1
                if member.dissolve_left > 0:
                    kept.append(member)
                # else: dissolve finished, drop it
            else:
                kept.append(member)
        self.members = kept

    @property
    def visible(self) -> list[CrewMember]:
        """The crew row: up to MAX_CREW, dissolving members included."""
        active = [m for m in self.members if not m.dissolving or m.dissolve_left > 0]
        return active[:MAX_CREW]

    @property
    def overflow(self) -> int:
        """Workers beyond the visible cap."""
        active = [m for m in self.members if not m.dissolving]
        return max(0, len(active) - MAX_CREW)

    def clear(self) -> None:
        self.members.clear()
