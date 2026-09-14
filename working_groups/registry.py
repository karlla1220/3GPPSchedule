"""Register each WG once. Disabled WGs are never imported or executed."""
from importlib import import_module
from shared.lifecycle import WorkingGroup

PIPELINES = {
    'ran1': 'working_groups.ran1.lifecycle',
    'ran-plenary': 'working_groups.ran_plenary.lifecycle',
}


def get_working_group(wg_id: str) -> WorkingGroup:
    return import_module(PIPELINES[wg_id])


def build_schedule(wg_id, options):
    return get_working_group(wg_id).build_schedule(options)
