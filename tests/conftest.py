"""Fixtures for Smart Heat tests. ZONES_FILE: a real house description (default: the one in staging)."""

import os

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"
ZONES_FILE = os.environ.get("SMART_HEAT_ZONES_FILE", os.path.expanduser("~/ha/staging/heating/zones.yaml"))


def pytest_configure(config):
    config.option.asyncio_mode = "auto"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Smart Heat depends on the recorder; it must be set up before hass."""
    yield
