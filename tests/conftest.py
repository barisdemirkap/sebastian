"""Pins the form of address so exact-wording assertions stay stable; the persona tests unpin it explicitly."""
import pytest

from sebastian import i18n


@pytest.fixture(autouse=True)
def _pinned_address():
    i18n.pin("efendim")
    i18n.set_rotation(0)
    yield
    i18n.pin(None)
    i18n.set_rotation(0)
    i18n.configure()        # back to the default language pack and persona, whatever a test selected
