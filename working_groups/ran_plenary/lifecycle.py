"""The fixed Plenary schedule participates in the same CI lifecycle as RAN1."""
from pathlib import Path

from shared.lifecycle import CheckResult
from .pipeline import build_schedule

input_paths = (Path('working_groups/ran_plenary'),)
persistent_paths = (Path('docs/ran-plenary'),)
cache_paths = ()


def check_updates():
    # The common runner detects changed code and missing output. No remote source.
    return CheckResult()


def prepare_build():
    pass


def reset_cache():
    pass
