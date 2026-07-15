from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import CandidateProfile, Evaluation, HistoryEntry, Offer


BLOCKING_FRICTION = frozenset({"captcha", "otp", "authentication", "account", "unknown_required_field"})
FRICTION_ALIASES = {
    "auth": "authentication",
    "login": "authentication",
    "unknown_field": "unknown_required_field",
}
STATUS_ORDER = {"submission_ready": 0, "shortlisted": 1, "review_required": 2, "duplicate": 3, "rejected": 4}


def normalize(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.lower()))


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    tracking_keys = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref_src"}

    def clean_query(raw_query: str) -> str:
        query = [
            (key, value)
            for key, value in parse_qsl(raw_query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in tracking_keys
        ]
        return urlencode(query)

    fragment = ""
    if parts.fragment.startswith(("/", "!/")):
        bang = "!" if parts.fragment.startswith("!") else ""
        spa = urlsplit(parts.fragment[1:] if bang else parts.fragment)
        fragment = bang + urlunsplit(("", "", spa.path.rstrip("/") or "/", clean_query(spa.query), ""))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, clean_query(parts.query), fragment))


def _is_duplicate(offer: Offer, history: list[HistoryEntry]) -> bool:
    target_url = canonical_url(offer.url)
    target_pair = (normalize(offer.company), normalize(offer.title))
    for entry in history:
        if canonical_url(entry.url) == target_url:
            return True
        if (normalize(entry.company), normalize(entry.title)) == target_pair:
            return True
    return False


def _base_result(offer: Offer, status: str, score: int, reasons: list[str], matched=(), missing=()) -> Evaluation:
    return Evaluation(
        offer_id=offer.offer_id,
        title=offer.title,
        company=offer.company,
        url=offer.url,
        status=status,
        score=max(0, min(100, score)),
        auto_submit=status == "submission_ready",
        reasons=tuple(reasons),
        matched_evidence=tuple(sorted(matched)),
        missing_required_evidence=tuple(sorted(missing)),
    )


def evaluate_offer(profile: CandidateProfile, offer: Offer, history: list[HistoryEntry]) -> Evaluation:
    reasons: list[str] = []
    title = normalize(offer.title)

    if not offer.active:
        return _base_result(offer, "rejected", 0, ["offer is inactive"])
    for term in profile.excluded_title_terms:
        normalized_term = normalize(term)
        if re.search(rf"(?:^|\s){re.escape(normalized_term)}(?:$|\s)", title):
            return _base_result(offer, "rejected", 0, [f"excluded title term: {normalized_term}"])
    if _is_duplicate(offer, history):
        return _base_result(offer, "duplicate", 0, ["offer already exists in application history"])

    title_match = any(normalize(target) in title or title in normalize(target) for target in profile.target_titles)
    if profile.target_titles and not title_match:
        return _base_result(offer, "rejected", 0, ["title outside target policy"])

    allowed_locations = tuple(normalize(item) for item in profile.allowed_locations)
    location = normalize(offer.location)
    if allowed_locations and (not location or not any(item in location or location in item for item in allowed_locations)):
        return _base_result(offer, "rejected", 0, [f"location outside policy: {location}"])

    contract = normalize(offer.contract)
    allowed_contracts = {normalize(item) for item in profile.allowed_contracts}
    if allowed_contracts and contract not in allowed_contracts:
        return _base_result(offer, "rejected", 0, [f"contract outside policy: {contract}"])

    evidence = {normalize(item) for item in profile.evidence_tags}
    required = {normalize(item) for item in offer.required_evidence}
    preferred = {normalize(item) for item in offer.preferred_evidence}
    matched = evidence & (required | preferred)
    missing = required - evidence

    known_languages = {normalize(item) for item in profile.known_languages}
    missing_languages = {normalize(item) for item in offer.required_languages} - known_languages
    raw_friction = {normalize(item).replace(" ", "_") for item in offer.friction}
    friction = {FRICTION_ALIASES.get(item, item) for item in raw_friction}
    blockers = sorted(friction & BLOCKING_FRICTION)
    unknown_friction = sorted(friction - BLOCKING_FRICTION)

    score = 40 if title_match else 10
    score += 30 if not required else round(30 * len(required & evidence) / len(required))
    score += 0 if not preferred else round(20 * len(preferred & evidence) / len(preferred))
    score += 5  # location passed
    score += 5  # contract passed

    if missing:
        reasons.append("missing required evidence: " + ", ".join(sorted(missing)))
    if missing_languages:
        reasons.append("unknown required language: " + ", ".join(sorted(missing_languages)))
    for blocker in blockers:
        reasons.append(f"application friction: {blocker}")
    for unknown in unknown_friction:
        reasons.append(f"unknown application friction: {unknown}")
    if missing or missing_languages or blockers or unknown_friction:
        return _base_result(offer, "review_required", score, reasons, matched, missing)

    if not offer.qualification_complete:
        reasons.append("collector did not verify requirements and application friction")
    if not offer.application_url_verified:
        reasons.append("application URL was not verified on the expected job-board domain")
    if offer.source != "manual" and not offer.publication_date_known:
        reasons.append("publication date is unknown for collected offer")
    if reasons:
        return _base_result(offer, "review_required", score, reasons, matched, missing)

    status = "submission_ready" if score >= profile.submission_score else "shortlisted"
    reasons.append("all hard requirements are verified")
    return _base_result(offer, status, score, reasons, matched, missing)


def rank_offers(profile: CandidateProfile, offers: list[Offer], history: list[HistoryEntry]) -> list[Evaluation]:
    seen = list(history)
    evaluations = []
    for offer in offers:
        evaluation = evaluate_offer(profile, offer, seen)
        evaluations.append(evaluation)
        seen.append(HistoryEntry(company=offer.company, title=offer.title, url=offer.url, status=evaluation.status))
    return sorted(evaluations, key=lambda item: (STATUS_ORDER[item.status], -item.score, item.company.lower(), item.title.lower()))
