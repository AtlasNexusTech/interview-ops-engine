import json
from pathlib import Path

import pytest

from interview_ops.cli import main


def test_cli_qualify_writes_ranked_json(tmp_path: Path):
    profile = {
        "target_titles": ["account manager"],
        "excluded_title_terms": ["assistant"],
        "evidence_tags": ["b2b", "contracts"],
        "allowed_locations": ["paris"],
        "allowed_contracts": ["cdi"],
        "known_languages": ["french"],
        "submission_score": 70,
    }
    offers = [{
        "offer_id": "a1",
        "title": "Account Manager",
        "company": "Sample Corp",
        "url": "https://careers.sample.test/a1",
        "location": "Paris",
        "contract": "CDI",
        "required_evidence": ["b2b"],
        "preferred_evidence": ["contracts"],
        "required_languages": ["french"],
        "friction": [],
        "active": True,
    }]
    profile_path = tmp_path / "profile.json"
    offers_path = tmp_path / "offers.json"
    output_path = tmp_path / "ranked.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    offers_path.write_text(json.dumps(offers), encoding="utf-8")

    exit_code = main(["qualify", "--profile", str(profile_path), "--offers", str(offers_path), "--output", str(output_path)])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload[0]["offer_id"] == "a1"
    assert payload[0]["status"] == "submission_ready"
    assert payload[0]["auto_submit"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("friction", "captcha"),
        ("active", "false"),
        ("required_evidence", "b2b"),
    ],
)
def test_cli_rejects_mistyped_offer_fields(tmp_path: Path, field: str, value):
    profile = {
        "target_titles": ["account manager"],
        "excluded_title_terms": ["assistant"],
        "evidence_tags": ["b2b"],
        "allowed_locations": ["paris"],
        "allowed_contracts": ["cdi"],
        "known_languages": ["french"],
        "submission_score": 70,
    }
    offer = {
        "offer_id": "a1",
        "title": "Account Manager",
        "company": "Sample Corp",
        "url": "https://careers.sample.test/a1",
        "location": "Paris",
        "contract": "CDI",
        "required_evidence": ["b2b"],
        "preferred_evidence": [],
        "required_languages": ["french"],
        "friction": [],
        "active": True,
    }
    offer[field] = value
    profile_path = tmp_path / "profile.json"
    offers_path = tmp_path / "offers.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    offers_path.write_text(json.dumps([offer]), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        main([
            "qualify",
            "--profile",
            str(profile_path),
            "--offers",
            str(offers_path),
            "--output",
            str(tmp_path / "out.json"),
        ])
