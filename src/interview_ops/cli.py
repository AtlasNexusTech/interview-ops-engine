from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .collector import Fetcher, SourceConfig, collect_sources
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
    for key in ("qualification_complete", "application_url_verified", "publication_date_known"):
        if key in values and not isinstance(values[key], bool):
            raise ValueError(f"{key} must be a boolean")
    for key in ("published_at", "valid_through"):
        value = values.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} must be an ISO date string or null")
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{key} must be an ISO date string or null") from exc
    if values.get("publication_date_known", False) and values.get("published_at") is None:
        raise ValueError("publication_date_known requires published_at")
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
    collect = subparsers.add_parser("collect", help="collect fresh jobs from explicitly configured official sources")
    collect.add_argument("--sources", required=True, help="JSON source configuration")
    collect.add_argument("--max-age-days", type=int, default=30)
    collect.add_argument("--timeout", type=float, default=10.0)
    collect.add_argument("--max-bytes", type=int, default=2_000_000)
    collect.add_argument("--profile", help="optionally score collected jobs with this profile")
    collect.add_argument("--history", help="optional JSONL application history (requires --profile)")
    collect.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None, *, fetcher: Fetcher | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "audit":
        audit_publishable_tree(Path(args.path))
        return 0
    if args.command == "collect":
        # Validate every local argument/file before a fetch can occur.
        if args.history and not args.profile:
            raise ValueError("--history requires --profile")
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 60:
            raise ValueError("timeout must be finite and between 0 and 60 seconds")
        if not 0 < args.max_bytes <= 10_000_000:
            raise ValueError("max-bytes must be between 1 and 10000000")
        if args.max_age_days < 0:
            raise ValueError("max-age-days must be non-negative")
        source_payload = _load_json(args.sources)
        if isinstance(source_payload, dict):
            source_payload = source_payload.get("sources")
        if not isinstance(source_payload, list):
            raise ValueError("sources file must be a list or an object containing a sources list")
        sources = [SourceConfig.from_dict(item) for item in source_payload]
        profile = _profile(_load_json(args.profile)) if args.profile else None
        history = _history(args.history) if args.history else []
        report = collect_sources(
            sources,
            fetcher=fetcher,
            max_age_days=args.max_age_days,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
        )
        payload = report.as_dict()
        if profile:
            payload["ranked"] = [
                asdict(item) for item in rank_offers(profile, report.jobs, history)
            ]
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return 2 if sources and all(result.error is not None for result in report.source_results) else 0
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
