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


def clear_paths(paths):
    """Remove disposable files/directories with the same rules for every WG."""
    for path in map(Path, paths):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def restore_staged(source, destination, *, rename=None, replace=False):
    """Restore a check artifact into a fresh build runner's WG directory."""
    source, destination = Path(source), Path(destination)
    if replace:
        clear_paths((destination,))
    if not source.is_dir():
        return
    rename = rename or {}
    for entry in source.iterdir():
        target = Path(rename.get(entry.name, destination / entry.name))
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_dir():
            shutil.copytree(entry, target, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, target)


class RejectedState(RuntimeError):
    """A build found its own saved state wrong and refuses to publish it.

    The build fails like any other, so an operator sees it.  Unlike other
    failures, rolling back is not enough: the rejected state would be reused
    as is by the next build.  ``discard`` lists the paths to remove after the
    rollback so that the next build regenerates them from scratch.
    """

    def __init__(self, message, discard=()):
        super().__init__(message)
        self.discard = tuple(Path(p) for p in discard)


@contextmanager
def preserve_on_failure(paths):
    """Rollback committed WG state as well as HTML on failed builds.

    A ``RejectedState`` failure also removes its ``discard`` paths after the
    rollback, including from an enclosing rollback that saw the same failure.
    """
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
        except BaseException as exc:
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
            if isinstance(exc, RejectedState):
                clear_paths(exc.discard)
            raise
