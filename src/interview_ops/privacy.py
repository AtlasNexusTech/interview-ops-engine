from __future__ import annotations

from pathlib import Path


class PrivacyViolation(RuntimeError):
    """Raised when a repository tree contains likely private candidate data."""


BLOCKED_EXACT_NAMES = {".env", "keypair.json", "applications.jsonl", "candidate.json", "profile.json"}
BLOCKED_SUFFIXES = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".pem", ".key"}
BLOCKED_NAME_PARTS = {"curriculum-vitae", "candidate-cv", "private-profile", "application-history"}
IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache"}


def audit_publishable_tree(root: Path) -> list[str]:
    if not root.exists():
        raise PrivacyViolation(f"audit root does not exist: {root}")
    if not root.is_dir():
        raise PrivacyViolation(f"audit root is not a directory: {root}")
    violations: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORED_DIRS for part in path.parts):
            continue
        name = path.name.lower()
        relative = path.relative_to(root).as_posix()
        if name in BLOCKED_EXACT_NAMES or name.startswith(".env.") or path.suffix.lower() in BLOCKED_SUFFIXES:
            violations.append(relative)
            continue
        if any(fragment in name for fragment in BLOCKED_NAME_PARTS):
            violations.append(relative)
    if violations:
        raise PrivacyViolation("private or sensitive files detected: " + ", ".join(sorted(violations)))
    return []
