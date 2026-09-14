"""The small lifecycle contract implemented independently by each WG.

Checks may stage downloads but must not advance committed state. Builds declare
their persistent output paths; disposable downloads and caches stay inside
their own WG namespace. CI restores
persistent paths if a build or rendering fails, allowing the next run to retry.
"""
from contextlib import contextmanager
import shutil
import tempfile

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


@contextmanager
def preserve_on_failure(paths):
    """Rollback committed WG state as well as HTML on failed builds."""
    with tempfile.TemporaryDirectory(prefix='wg-build-') as directory:
        saved = []
        for i, path in enumerate(paths):
            path = Path(path)
            backup = Path(directory) / str(i)
            exists = path.exists()
            if exists:
                if path.is_dir():
                    shutil.copytree(path, backup)
                else:
                    shutil.copy2(path, backup)
            saved.append((path, backup, exists))
        try:
            yield
        except BaseException:
            for path, backup, existed in saved:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                if existed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir():
                        shutil.copytree(backup, path)
                    else:
                        shutil.copy2(backup, path)
            raise
