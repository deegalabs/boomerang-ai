"""Shared fixtures for the Boomerang AI test suite."""
from __future__ import annotations

import pytest

from boomerang.config import load_config


@pytest.fixture
def cfg():
    return load_config()
