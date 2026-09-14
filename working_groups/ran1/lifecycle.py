"""RAN1's CI adapter: discovery, external-file transfer, parsing and caches."""
from pathlib import Path
import shutil

input_paths = (Path('working_groups/ran1'), Path('ref_in_manual/ran1'))
persistent_paths = (Path('docs/ran1'), Path('downloads/ran1/extra_files'))
cache_paths = (Path('.cache/ran1'),)
TRANSFER_DIR = Path('.ci/transfers/ran1')


def check_updates():
    from .check_update import check_updates as check
    return check(staging_dir=TRANSFER_DIR)


def prepare_build():
    """Consume check's downloaded files on a fresh build runner."""
    from .downloader import EXTRA_FILES_DIR, EXTRA_FILES_STATE_PATH
    if not TRANSFER_DIR.exists():
        return
    EXTRA_FILES_DIR.mkdir(parents=True, exist_ok=True)
    EXTRA_FILES_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    for source in TRANSFER_DIR.iterdir():
        if source.is_file():
            destination = (EXTRA_FILES_STATE_PATH if source.name == '.extra_files_state.json'
                           else EXTRA_FILES_DIR / source.name)
            shutil.copy2(source, destination)


def reset_cache():
    from .downloader import EXTRA_FILES_DIR, EXTRA_FILES_STATE_PATH
    from .slot_state import clear_all_slot_states
    for path in cache_paths:
        shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(EXTRA_FILES_DIR, ignore_errors=True)
    EXTRA_FILES_STATE_PATH.unlink(missing_ok=True)
    for name in ('.schedule_state.json', 'agenda_item_description.json'):
        (Path('docs/ran1') / name).unlink(missing_ok=True)
    clear_all_slot_states()


def build_schedule(options):
    from .pipeline import build_schedule as build
    return build(options)
