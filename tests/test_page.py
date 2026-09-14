"""Common page parts can be reused without the Gantt renderer."""
from dataclasses import replace

import pytest

from shared.page import normalize_presentation, render_header
from schedule_fixture import build_schedule


def test_header_escapes_site_metadata_and_keeps_demo_disclosure():
    schedule = replace(build_schedule(), contact_email='old@example.com')
    html = render_header(schedule, presentation={
        'creator': '<script>bad()</script>', 'contact_name': '<Support>',
        'contact_email': 'new@example.com', 'notice': 'Official'})
    assert '<script>' not in html and '&lt;Support&gt;' in html
    assert 'mailto:new@example.com' in html and 'old@example.com' not in html
    assert 'Demo schedule' in html and 'Official' not in html
    assert 'id="tz-now"' in html


def test_empty_site_contact_does_not_restore_legacy_snapshot_contact():
    assert 'mailto:' not in render_header(replace(build_schedule(), contact_email='old@example.com'))


@pytest.mark.parametrize('value', [[], {'creator': None}, {'contact_emali': 'typo'},
    {'contact_email': 'name@example.com?subject=unexpected'},
    {'contact_email': 'name@example.com\nBcc:other@example.com'}])
def test_invalid_site_metadata_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_presentation(value)
