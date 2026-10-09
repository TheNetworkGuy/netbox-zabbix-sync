"""Fixtures shared by the mocked (non-functional) test suite.

The builders themselves live in tests/fakes so unittest-style classes can use
them too; these fixtures just hand pytest-style tests a fresh instance.
"""

import pytest

from tests.fakes import FakeNetBox, zabbix_api


@pytest.fixture
def netbox() -> FakeNetBox:
    """An empty in-memory NetBox; build the objects a test needs from it."""
    return FakeNetBox()


@pytest.fixture
def zabbix():
    """A Zabbix API mock with one hostgroup, one template and no hosts."""
    return zabbix_api()
