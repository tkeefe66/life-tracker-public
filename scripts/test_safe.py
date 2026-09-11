"""Run pytest without loading credentials or selecting a production database."""
import os
from pathlib import Path
import sys
import tempfile

import dotenv


def main():
    dotenv.load_dotenv = lambda *args, **kwargs: False
    for name in list(os.environ):
        if name.startswith(("GOOGLE_", "BACKUP_", "COACH_", "PG", "TELEGRAM_", "SIMPLEFIN_", "ANTHROPIC_")):
            os.environ.pop(name)
    with tempfile.TemporaryDirectory(prefix="on-track-tests-") as tmp:
        os.environ.update(ANTHROPIC_API_KEY="test", APP_PASSWORD="test-password",
                          DATABASE_URL="", DATABASE_PATH=str(Path(tmp) / "test.db"))
        root = Path(__file__).resolve().parents[1]
        os.chdir(root)
        sys.path.insert(0, str(root))
        import pytest
        return pytest.main(sys.argv[1:] or ["tests/", "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
