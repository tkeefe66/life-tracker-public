"""Exercise the exact index bytes, independent of the working tree."""
import json
import os
from pathlib import Path
import secrets
import shutil
import string
import subprocess
import sys

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "check_publication.py"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "-c", "init.templateDir=", "init", "-q")
    return tmp_path


def check(repo, env=None):
    return subprocess.run([sys.executable, str(CHECKER), "--root", str(repo)],
                          capture_output=True, text=True, env=env)


def test_clean_first_index_ignores_unstaged_secret(repo):
    # Mutation: scan working-tree bytes instead of staged bytes.
    path = repo / "file with spaces.txt"
    path.write_text("ordinary documentation\n")
    git(repo, "add", "--", path.name)
    path.write_text("GITHUB_TOKEN=ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36)))
    assert check(repo).returncode == 0


def test_staged_secret_cannot_be_hidden_by_clean_worktree(repo):
    # Mutation: inspect the clean working copy and miss the staged credential.
    path = repo / "file with spaces.txt"
    token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
    path.write_text("GITHUB_TOKEN=" + token)
    git(repo, "add", "--", path.name)
    path.write_text("clean working copy\n")
    result = check(repo)
    assert result.returncode != 0
    assert "Secret scan rejected" in result.stderr
    assert token not in result.stdout + result.stderr


def test_private_record_and_private_artifact_are_rejected(repo):
    # Mutation: omit private marker matching or allow force-added environment files.
    (repo / ".git" / "publication-private-patterns.json").write_text(json.dumps(["PRIVATE_RECORD_SENTINEL"]))
    path = repo / "example.txt"
    path.write_text("PRIVATE_RECORD_SENTINEL")
    git(repo, "add", "example.txt")
    result = check(repo)
    assert result.returncode != 0
    assert "Private publication marker detected" in result.stderr
    assert "PRIVATE_RECORD_SENTINEL" not in result.stdout + result.stderr
    path.write_text("safe")
    git(repo, "add", "example.txt")
    (repo / ".env.local").write_text("placeholder")
    git(repo, "add", "-f", ".env.local")
    assert check(repo).returncode != 0
    git(repo, "rm", "--cached", ".env.local")
    assert check(repo).returncode == 0


def test_missing_scanner_fails_closed(repo, tmp_path):
    # Mutation: return success when the required scanner is unavailable.
    bin_dir = tmp_path / "only-git"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    env = dict(os.environ, PATH=str(bin_dir))
    result = check(repo, env)
    assert result.returncode != 0
    assert "gitleaks is required" in result.stderr
