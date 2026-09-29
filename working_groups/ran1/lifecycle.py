"""RAN1's CI adapter: discovery, external-file transfer, parsing and caches."""
from pathlib import Path
from shared.lifecycle import clear_paths, restore_staged

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
    restore_staged(TRANSFER_DIR, EXTRA_FILES_DIR,
                   rename={'.extra_files_state.json': EXTRA_FILES_STATE_PATH})


def reset_cache():
    from .downloader import EXTRA_FILES_DIR, EXTRA_FILES_STATE_PATH
    from .slot_state import clear_all_slot_states
    clear_paths((*cache_paths, EXTRA_FILES_DIR, EXTRA_FILES_STATE_PATH,
                 Path('docs/ran1/.schedule_state.json'), Path('docs/ran1/agenda_item_description.json')))
    clear_all_slot_states()


def build_schedule(options):
    from .pipeline import build_schedule as build
    return build(options)
