"""What the Home Assistant side of the suite needs before any of it can run.

``pytest-homeassistant-custom-component`` registers itself as a pytest plugin through an
entry point, so it loads wherever it is installed and nowhere else. That is exactly the
property this repository wants: the pure jobs install no Home Assistant at all, so nothing
here reaches them, and ``tests/conftest.py`` stays the only thing they see.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _load_custom_integrations(enable_custom_integrations: Any) -> Generator[None]:
    """Let Home Assistant see ``custom_components/mvave`` at all.

    Requested for every test rather than by name in each: a custom integration that is not
    enabled does not fail loudly, it simply is not found, and the resulting error is about
    an unknown handler rather than about this.
    """
    yield
