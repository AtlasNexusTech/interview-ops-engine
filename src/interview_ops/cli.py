from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .engine import rank_offers
from .models import CandidateProfile, HistoryEntry, Offer
from .privacy import audit_publishable_tree


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _string_list(payload: dict, key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return value


def _profile(payload: dict) -> CandidateProfile:
    score = payload.get("submission_score", 70)
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError("submission_score must be an integer between 0 and 100")
    return CandidateProfile(
        target_titles=tuple(_string_list(payload, "target_titles")),
        excluded_title_terms=tuple(_string_list(payload, "excluded_title_terms")),
        evidence_tags=frozenset(_string_list(payload, "evidence_tags")),
        allowed_locations=tuple(_string_list(payload, "allowed_locations")),
        allowed_contracts=tuple(_string_list(payload, "allowed_contracts")),
        known_languages=frozenset(_string_list(payload, "known_languages")),
        submission_score=score,
    )


def _offer(payload: dict) -> Offer:
    values = dict(payload)
    for key in ("offer_id", "title", "company", "url", "location", "contract"):
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if not values["url"].startswith(("https://", "http://")):
        raise ValueError("url must be an http(s) URL")
    for key in ("required_evidence", "preferred_evidence", "required_languages", "friction"):
        values[key] = tuple(_string_list(values, key))
    if not isinstance(values.get("active"), bool):
        raise ValueError("active must be a boolean")
    return Offer(**values)


def _history(path: str | None) -> list[HistoryEntry]:
    if not path:
        return []
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(HistoryEntry(**json.loads(line)))
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="interview-ops", description="Privacy-first deterministic job application triage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify = subparsers.add_parser("qualify", help="score, guard and rank job offers")
    qualify.add_argument("--profile", required=True)
    qualify.add_argument("--offers", required=True)
    qualify.add_argument("--history")
    qualify.add_argument("--output", required=True)
    audit = subparsers.add_parser("audit", help="fail if a tree contains likely private candidate files")
    audit.add_argument("path", nargs="?", default=".")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        audit_publishable_tree(Path(args.path))
        return 0
    profile = _profile(_load_json(args.profile))
    offers = [_offer(item) for item in _load_json(args.offers)]
    ranked = rank_offers(profile, offers, _history(args.history))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([asdict(item) for item in ranked], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
