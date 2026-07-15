from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    target_titles: tuple[str, ...]
    excluded_title_terms: tuple[str, ...]
    evidence_tags: frozenset[str]
    allowed_locations: tuple[str, ...]
    allowed_contracts: tuple[str, ...]
    known_languages: frozenset[str]
    submission_score: int = 70


@dataclass(frozen=True, slots=True)
class Offer:
    offer_id: str
    title: str
    company: str
    url: str
    location: str
    contract: str
    required_evidence: tuple[str, ...] = ()
    preferred_evidence: tuple[str, ...] = ()
    required_languages: tuple[str, ...] = ()
    friction: tuple[str, ...] = ()
    active: bool = True
    published_at: str | None = None
    publication_date_known: bool = False
    description: str = ""
    source: str = "manual"
    qualification_complete: bool = True
    application_url_verified: bool = True
    valid_through: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    company: str
    title: str
    url: str
    status: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    offer_id: str
    title: str
    company: str
    url: str
    status: str
    score: int
    auto_submit: bool
    reasons: tuple[str, ...]
    matched_evidence: tuple[str, ...]
    missing_required_evidence: tuple[str, ...]
