import json
from pathlib import Path

import pytest

from interview_ops.cli import main


class FakeFetcher:
    def __init__(self, body: str):
        self.body = body

    def fetch(self, url, *, timeout, max_bytes, user_agent):
        return self.body.encode()


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


def test_cli_collect_writes_report_and_can_score_collected_jobs(tmp_path: Path):
    sources_path = tmp_path / "sources.json"
    profile_path = tmp_path / "profile.json"
    output_path = tmp_path / "collected.json"
    sources_path.write_text(json.dumps({"sources": [
        {"type": "lever", "company": "Example", "identifier": "example"}
    ]}), encoding="utf-8")
    profile_path.write_text(json.dumps({
        "target_titles": ["account manager"], "excluded_title_terms": [],
        "evidence_tags": [], "allowed_locations": ["paris"],
        "allowed_contracts": ["cdi"], "known_languages": [], "submission_score": 70,
    }), encoding="utf-8")
    response = json.dumps([{
        "id": "1", "text": "Account Manager", "hostedUrl": "https://jobs.lever.co/example/1",
        "categories": {"location": "Paris", "commitment": "CDI"},
    }])

    result = main([
        "collect", "--sources", str(sources_path), "--max-age-days", "14",
        "--profile", str(profile_path), "--output", str(output_path),
    ], fetcher=FakeFetcher(response))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["jobs"][0]["source"] == "lever:example"
    assert payload["source_results"][0]["accepted"] == 1
    assert payload["errors"] == []
    assert payload["ranked"][0]["offer_id"] == "lever:example:1"
    assert payload["ranked"][0]["status"] == "review_required"
    assert payload["ranked"][0]["auto_submit"] is False


def test_cli_rejects_history_without_profile_and_bad_bounds_before_fetch(tmp_path: Path):
    sources = tmp_path / "sources.json"
    history = tmp_path / "history.jsonl"
    sources.write_text(json.dumps({"sources": [{"type": "lever", "company": "X", "identifier": "x"}]}), encoding="utf-8")
    history.write_text("", encoding="utf-8")

    class MustNotFetch:
        def fetch(self, *args, **kwargs):
            raise AssertionError("network must not be reached")

    for extra, message in [
        (["--history", str(history)], "--history requires --profile"),
        (["--timeout", "inf"], "timeout"),
        (["--timeout", "61"], "timeout"),
    ]:
        with pytest.raises(ValueError, match=message):
            main(["collect", "--sources", str(sources), "--output", str(tmp_path / "out.json"), *extra], fetcher=MustNotFetch())


def test_cli_all_sources_failed_returns_nonzero_but_preserves_report(tmp_path: Path):
    sources = tmp_path / "sources.json"
    output = tmp_path / "report.json"
    sources.write_text(json.dumps({"sources": [{"type": "lever", "company": "X", "identifier": "x"}]}), encoding="utf-8")

    class FailedFetcher:
        def fetch(self, *args, **kwargs):
            raise RuntimeError("boom")

    code = main(["collect", "--sources", str(sources), "--output", str(output)], fetcher=FailedFetcher())
    assert code != 0
    assert json.loads(output.read_text(encoding="utf-8"))["errors"]


def test_cli_validates_new_offer_metadata_types_and_consistency(tmp_path: Path):
    profile = {
        "target_titles": [], "excluded_title_terms": [], "evidence_tags": [],
        "allowed_locations": [], "allowed_contracts": [], "known_languages": [],
    }
    base = {
        "offer_id": "a", "title": "Role", "company": "Example",
        "url": "https://jobs.example.test/a", "location": "Paris", "contract": "CDI",
        "required_evidence": [], "preferred_evidence": [], "required_languages": [],
        "friction": [], "active": True,
    }
    profile_path = tmp_path / "profile.json"
    offers_path = tmp_path / "offers.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    for change, message in [
        ({"valid_through": 123}, "valid_through"),
        ({"published_at": 123}, "published_at"),
        ({"publication_date_known": True, "published_at": None}, "publication_date_known"),
    ]:
        offers_path.write_text(json.dumps([{**base, **change}]), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            main(["qualify", "--profile", str(profile_path), "--offers", str(offers_path),
                  "--output", str(tmp_path / "out.json")])
