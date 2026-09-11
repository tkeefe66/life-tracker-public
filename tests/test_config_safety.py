"""Configuration must not permit a blank login password."""
from pathlib import Path
import runpy

import pytest


def test_blank_password_rejected(monkeypatch):
    # Mutation: allow the empty template password to become a valid login.
    monkeypatch.setenv("APP_PASSWORD", "")
    with pytest.raises(ValueError, match="APP_PASSWORD"):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "config.py"))
