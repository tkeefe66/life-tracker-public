"""Private backfill configuration never belongs in published source."""
import json
from unittest.mock import Mock

import pytest

from scripts import simplefin_backfill as backfill


def test_no_hardcoded_roles_are_used():
    # Mutation: retaining the real built-in seed list instead of private config.
    assert not getattr(backfill, "ROLE_SEEDS", [])


def test_private_seed_file_is_loaded_and_missing_default_is_empty(tmp_path):
    # Mutation: ignore the private file and return empty seeds for every input.
    path = tmp_path / "roles.json"
    assert backfill.load_role_seeds(path) == []
    path.write_text(json.dumps([["example bank", "1001", "bills"]]))
    assert backfill.load_role_seeds(path) == [("example bank", "1001", "bills")]


def test_invalid_role_file_fails_without_echoing_private_data(tmp_path):
    # Mutation: accept an invalid role or include its private contents in errors.
    path = tmp_path / "roles.json"
    path.write_text(json.dumps([["private sentinel", "1001", "invalid"]]))
    with pytest.raises(ValueError) as exc:
        backfill.load_role_seeds(path)
    assert "private sentinel" not in str(exc.value)


def test_seed_roles_preserves_existing_user_roles():
    # Mutation: overwrite a user-assigned role instead of seeding unknown only.
    db = Mock()
    db.get_bank_accounts.return_value = [
        {"simplefin_id": "a", "org": "Example Bank", "name": "Checking 1001", "role": "unknown"},
        {"simplefin_id": "b", "org": "Example Bank", "name": "Checking 1001", "role": "savings"},
    ]
    assert backfill.seed_roles(db, [("example bank", "1001", "bills")]) == 1
    db.set_bank_account_role.assert_called_once_with("a", "bills")
