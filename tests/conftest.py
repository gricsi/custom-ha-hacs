"""Small identity fixture for executor dispatch tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import Context


@pytest.fixture
def administrator():
    def configure(hass):
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=True)))
        return SimpleNamespace(context=Context(user_id="admin"))

    return configure
