"""The small lifecycle contract implemented independently by each WG.

Checks may stage downloads but must not advance committed state. Builds declare
their persistent output paths; disposable downloads and caches stay inside
their own WG namespace. CI restores
persistent paths if a build or rendering fails, allowing the next run to retry.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from shared.schedule import Schedule


@dataclass
class CheckResult:
    changed: bool = False
    reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class BuildOptions:
    local: str | None = None
    no_download: bool = False
    rebuild_slots: bool = False


class WorkingGroup(Protocol):
    # All paths are relative to the repository root, and belong to this WG.
    input_paths: tuple[Path, ...]
    persistent_paths: tuple[Path, ...]
    cache_paths: tuple[Path, ...]

    def check_updates(self) -> CheckResult: ...
    def prepare_build(self) -> None: ...
    def reset_cache(self) -> None: ...
    def build_schedule(self, options: BuildOptions) -> Schedule: ...
