from pathlib import Path

import pytest

from interview_ops.privacy import PrivacyViolation, audit_publishable_tree


def test_privacy_audit_rejects_cv_and_secret_files(tmp_path: Path):
    (tmp_path / "candidate-cv.pdf").write_bytes(b"pdf")
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")

    with pytest.raises(PrivacyViolation) as error:
        audit_publishable_tree(tmp_path)

    message = str(error.value)
    assert "candidate-cv.pdf" in message
    assert ".env" in message


def test_privacy_audit_accepts_fictional_examples(tmp_path: Path):
    (tmp_path / "profile.example.json").write_text('{"name":"Sample Candidate"}', encoding="utf-8")
    assert audit_publishable_tree(tmp_path) == []


@pytest.mark.parametrize("name", [".env.production", "private.key", "certificate.pem", "profile.json"])
def test_privacy_audit_rejects_all_documented_private_patterns(tmp_path: Path, name: str):
    (tmp_path / name).write_text("private", encoding="utf-8")
    with pytest.raises(PrivacyViolation):
        audit_publishable_tree(tmp_path)


def test_privacy_audit_rejects_missing_or_non_directory_root(tmp_path: Path):
    with pytest.raises(PrivacyViolation, match="does not exist"):
        audit_publishable_tree(tmp_path / "missing")
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(PrivacyViolation, match="not a directory"):
        audit_publishable_tree(file_path)
