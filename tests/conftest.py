"""Shared pytest fixtures."""
import os
import tempfile
from unittest.mock import MagicMock

# Set required env vars BEFORE any project imports so config.py doesn't raise KeyError.
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("APP_PASSWORD", "test-password")

import pytest


@pytest.fixture
def temp_db_path(monkeypatch):
    """Fresh SQLite DB per test, isolated from local dev DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", path)
    monkeypatch.setenv("DATABASE_URL", "")
    import importlib
    import config
    importlib.reload(config)
    import database
    importlib.reload(database)
    database.initialize_db()
    yield path
    os.unlink(path)


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Replace anthropic.Anthropic with a mock that returns canned JSON."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="{}")]
    mock_client.messages.create.return_value = mock_response

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: mock_client)
    try:
        import ai_metrics
        ai_metrics._client = None
    except ImportError:
        pass
    return mock_client
