import pytest

from shared import remote_files


@pytest.fixture(autouse=True)
def fresh_listings():
    """Each test sees its own mock server, not listings an earlier test fetched."""
    remote_files.new_run()
    yield
    remote_files.new_run()
