"""One source of truth for enabled working groups in local builds and CI."""
import json
from pathlib import Path


def load_site_config(path=Path('site.json')):
    from working_groups.registry import PIPELINES

    config = json.loads(path.read_text(encoding='utf-8'))
    groups = config['working_groups']
    ids = [g['id'] for g in groups]
    if len(ids) != len(set(ids)) or any(wg not in PIPELINES for wg in ids):
        raise ValueError('Site WG ids must be unique registered pipelines')
    if any(not isinstance(g.get('enabled', True), bool) for g in groups):
        raise ValueError('WG enabled must be a boolean')
    config['working_groups'] = [g for g in groups if g.get('enabled', True)]
    if config['default_wg'] not in [g['id'] for g in config['working_groups']]:
        raise ValueError('default_wg must be an enabled WG')
    from shared.page import normalize_presentation
    config["presentation"] = normalize_presentation(config.get("presentation"))
    return config
