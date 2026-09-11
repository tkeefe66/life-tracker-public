"""Scan exactly the Git index (default) or a committed tree before publication.

Uses only the standard library. Never imports application configuration. Private
matching values may live in .git/publication-private-patterns.json, never source.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)


def rejected_path(path):
    name = path.name.lower()
    return (
        (name.startswith(".env") and name != ".env.example")
        or name in {"service_account.json", "client_secret.json", "calendar_token.json",
                    "role-seeds.json", "security-life-tracker.md.docx"}
        or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".dump", ".enc", ".pem", ".key"}
        or any(part in {".claude", ".agents", ".impeccable", ".readiness-audit",
                        "node_modules", "venv", ".venv"} for part in path.parts)
        or path.parts[:2] in {("frontend", "dist"), ("docs", "superpowers")}
        or name.startswith("simplefin_") and name.endswith(".json")
    )


def scan(root, ref=None):
    scanner = shutil.which("gitleaks")
    if not scanner:
        raise ValueError("gitleaks is required; install Gitleaks 8.30.1 and retry.")
    records = git(root, "ls-tree", "-rz", ref) if ref else git(root, "ls-files", "--stage", "-z")
    private_path = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "publication-private-patterns.json").strip()))
    if not private_path.is_absolute():
        private_path = root / private_path
    patterns = []
    if private_path.exists():
        try:
            patterns = json.loads(private_path.read_text())
        except (OSError, ValueError):
            raise ValueError("Private publication patterns cannot be read; repair the local Git configuration.") from None
        if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
            raise ValueError("Private publication patterns must be a nonempty-string list.")
    with tempfile.TemporaryDirectory(prefix="publication-") as tmp:
        temp = Path(tmp)
        export = temp / "content"
        export.mkdir()
        for entry in records.split(b"\0"):
            if not entry:
                continue
            metadata, filename = entry.split(b"\t", 1)
            fields = metadata.split()
            mode, oid = fields[0], fields[2] if ref else fields[1]
            if not ref and fields[2] != b"0":
                raise ValueError("Resolve merge conflicts before publication.")
            path = PurePosixPath(os.fsdecode(filename))
            if path.is_absolute() or ".." in path.parts or mode not in {b"100644", b"100755"}:
                raise ValueError("Publication contains an unsupported path, link, or submodule; review it first.")
            if rejected_path(path):
                raise ValueError("Private or generated artifact staged; remove it from the index before committing.")
            content = git(root, "cat-file", "blob", oid.decode())
            lowered = content.lower()
            if any(pattern.lower().encode() in lowered for pattern in patterns):
                raise ValueError("Private publication marker detected; replace personal records with synthetic data.")
            destination = export.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        config = temp / "scanner.toml"
        config.write_text("[extend]\nuseDefault = true\n")
        env = dict(os.environ)
        env.pop("GITLEAKS_CONFIG", None)
        env.pop("GITLEAKS_CONFIG_TOML", None)
        result = subprocess.run([
            scanner, "dir", str(export), "--config", str(config), "--redact=100",
            "--ignore-gitleaks-allow", "--gitleaks-ignore-path", str(temp / "no-ignore"),
            "--max-archive-depth=3", "--no-banner",
        ], env=env, capture_output=True)
        if result.returncode:
            raise ValueError("Secret scan rejected the candidate or could not complete; inspect locally with Gitleaks --redact.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--ref", help="Scan a committed tree instead of the index")
    args = parser.parse_args()
    try:
        scan(args.root.resolve(), args.ref)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Only our closed explanatory messages are printed. Git/OS errors may
        # contain private paths and must not expose their original text.
        message = str(exc) if isinstance(exc, ValueError) else "Publication check failed; verify Git and file access, then retry."
        print(message, file=sys.stderr)
        return 1
    print("Publication checks passed for the selected Git content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
