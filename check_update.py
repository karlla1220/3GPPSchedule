"""Compatibility entrypoint: check every enabled WG and emit the CI plan."""
from ci import check_site, outputs

if __name__ == '__main__':
    result = check_site()
    outputs({key: result[key] for key in ['build_required', 'build_ids', 'has_errors']})
