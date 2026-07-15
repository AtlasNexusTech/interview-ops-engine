from interview_ops.engine import canonical_url, evaluate_offer, rank_offers
from interview_ops.models import CandidateProfile, HistoryEntry, Offer


def profile() -> CandidateProfile:
    return CandidateProfile(
        target_titles=("customer success manager", "account manager", "business developer"),
        excluded_title_terms=("assistant", "stage", "alternance", "growth manager"),
        evidence_tags=frozenset({"b2b", "portfolio", "contracts", "crm", "english", "automotive"}),
        allowed_locations=("paris", "ile-de-france", "remote france"),
        allowed_contracts=("cdi", "cdd"),
        known_languages=frozenset({"french", "english"}),
        submission_score=70,
    )


def offer(**overrides) -> Offer:
    values = {
        "offer_id": "job-1",
        "title": "Customer Success Manager",
        "company": "Example Industries",
        "url": "https://jobs.example.test/job-1",
        "location": "Paris",
        "contract": "CDI",
        "required_evidence": ("b2b", "portfolio"),
        "preferred_evidence": ("crm", "automotive"),
        "required_languages": ("french", "english"),
        "friction": (),
        "active": True,
    }
    values.update(overrides)
    return Offer(**values)


def test_excluded_title_is_rejected():
    result = evaluate_offer(profile(), offer(title="Assistant Customer Success"), [])
    assert result.status == "rejected"
    assert "excluded title term: assistant" in result.reasons


def test_inactive_offer_is_rejected():
    result = evaluate_offer(profile(), offer(active=False), [])
    assert result.status == "rejected"
    assert "offer is inactive" in result.reasons


def test_duplicate_canonical_url_is_not_reopened():
    history = [HistoryEntry(company="Example Industries", title="Customer Success Manager", url="https://jobs.example.test/job-1?utm_source=board", status="submitted")]
    result = evaluate_offer(profile(), offer(), history)
    assert result.status == "duplicate"
    assert result.auto_submit is False


def test_unknown_required_evidence_requires_human_review():
    result = evaluate_offer(profile(), offer(required_evidence=("b2b", "saas")), [])
    assert result.status == "review_required"
    assert result.missing_required_evidence == ("saas",)
    assert result.auto_submit is False


def test_captcha_or_otp_blocks_automatic_submission():
    for blocker in ("captcha", "otp", "auth", "authentication", "unknown_field", "unknown_required_field"):
        result = evaluate_offer(profile(), offer(friction=(blocker,)), [])
        assert result.status == "review_required"
        assert result.auto_submit is False
        assert any(reason.startswith("application friction:") for reason in result.reasons)


def test_empty_location_is_rejected_when_location_policy_exists():
    result = evaluate_offer(profile(), offer(location=""), [])
    assert result.status == "rejected"
    assert result.auto_submit is False
    assert "location outside policy" in result.reasons[0]


def test_unrelated_title_cannot_be_submission_ready():
    result = evaluate_offer(profile(), offer(title="Chief Financial Officer"), [])
    assert result.status == "rejected"
    assert result.auto_submit is False
    assert "title outside target policy" in result.reasons


def test_unknown_friction_fails_closed():
    result = evaluate_offer(profile(), offer(friction=("security_question",)), [])
    assert result.status == "review_required"
    assert result.auto_submit is False
    assert "unknown application friction: security_question" in result.reasons


def test_canonical_url_removes_tracking_but_preserves_functional_ids():
    first = canonical_url("https://jobs.example.test/opening?id=1&utm_source=board")
    second = canonical_url("https://jobs.example.test/opening?id=2&utm_source=board")
    first_spa = canonical_url("https://jobs.example.test/#/opening/101?utm_source=one")
    same_spa = canonical_url("https://jobs.example.test/#/opening/101?utm_source=two")
    second_spa = canonical_url("https://jobs.example.test/#/opening/202")
    anchor_one = canonical_url("https://jobs.example.test/opening/101#apply")
    anchor_two = canonical_url("https://jobs.example.test/opening/101#description")
    assert first == "https://jobs.example.test/opening?id=1"
    assert second == "https://jobs.example.test/opening?id=2"
    assert first != second
    assert first_spa.endswith("#/opening/101")
    assert same_spa == first_spa
    assert second_spa.endswith("#/opening/202")
    assert first_spa != second_spa
    assert anchor_one == anchor_two == "https://jobs.example.test/opening/101"


def test_strong_verified_fit_is_submission_ready():
    result = evaluate_offer(profile(), offer(), [])
    assert result.status == "submission_ready"
    assert result.score >= 70
    assert result.auto_submit is True
    assert result.missing_required_evidence == ()


def test_unqualified_collected_offer_always_requires_review_but_legacy_offer_does_not():
    collected = offer(
        source="lever:example",
        qualification_complete=False,
        publication_date_known=True,
    )

    result = evaluate_offer(profile(), collected, [])

    assert result.status == "review_required"
    assert result.auto_submit is False
    assert "collector did not verify requirements and application friction" in result.reasons
    assert evaluate_offer(profile(), offer(), []).status == "submission_ready"


def test_rank_offers_marks_duplicates_inside_the_same_batch():
    first = offer(offer_id="first", url="https://jobs.example.test/shared?utm_source=one")
    second = offer(offer_id="second", url="https://jobs.example.test/shared?utm_source=two")

    ranked = rank_offers(profile(), [first, second], [])

    by_id = {item.offer_id: item for item in ranked}
    assert by_id["first"].status == "submission_ready"
    assert by_id["second"].status == "duplicate"
    assert by_id["second"].auto_submit is False


def test_rank_offers_prioritizes_ready_fit_and_keeps_rejections_last():
    offers = [
        offer(offer_id="bad", title="Assistant Account Manager", url="https://jobs.example.test/bad"),
        offer(offer_id="medium", title="Account Manager", url="https://jobs.example.test/medium", preferred_evidence=()),
        offer(offer_id="best", url="https://jobs.example.test/best"),
    ]
    ranked = rank_offers(profile(), offers, [])
    assert [item.offer_id for item in ranked] == ["best", "medium", "bad"]
    assert ranked[-1].status == "rejected"
