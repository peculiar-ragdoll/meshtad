"""pytest configuration and shared fixtures."""
from __future__ import annotations

import pytest

# No global fixtures needed -- each test file manages its tmp_db and db_thread.

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
