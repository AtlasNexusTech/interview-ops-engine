# Interview Ops Engine

[![CI](https://github.com/AtlasNexusTech/interview-ops-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/AtlasNexusTech/interview-ops-engine/actions/workflows/ci.yml)

A deterministic, privacy-first engine for qualifying, deduplicating and ranking job opportunities before any application automation touches an ATS.

## Why

Most job-search automations optimize for application volume. That creates duplicates, weak-fit submissions and unsafe attempts to answer unknown screening questions.

Interview Ops Engine uses explicit evidence and hard gates instead:

- rejects excluded titles, inactive listings, incompatible locations and contracts;
- deduplicates canonical URLs and company/title pairs;
- distinguishes verified evidence from missing requirements;
- blocks automatic submission when CAPTCHA, OTP, authentication or unknown fields appear;
- produces transparent scores, reasons and operational states;
- audits a repository tree for likely CVs, secrets and private application histories.

It does **not** bypass CAPTCHA, authentication or access controls, and it does not fabricate candidate facts.

## Operational states

- `submission_ready` — all hard requirements are verified, the score passes policy and no human gate is present.
- `shortlisted` — compatible but below the configured submission threshold.
- `review_required` — a required fact is unknown or the application path contains a human/security gate.
- `duplicate` — the offer already exists in history.
- `rejected` — inactive or outside explicit policy.

`auto_submit=true` is emitted only for `submission_ready`. This project deliberately stops before browser/ATS submission; connectors should consume the decision output and preserve these gates.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

interview-ops qualify \
  --profile examples/profile.example.json \
  --offers examples/offers.example.json \
  --history examples/history.example.jsonl \
  --output out/ranked.json

python -m json.tool out/ranked.json
```

Run the privacy gate before publishing:

```bash
interview-ops audit .
```

Run validation:

```bash
pytest
ruff check .
```

## Input contract

### Candidate profile

The profile contains policy and **evidence tags**, not prose generated from a résumé:

```json
{
  "target_titles": ["customer success manager", "account manager"],
  "excluded_title_terms": ["assistant", "stage", "alternance"],
  "evidence_tags": ["b2b", "portfolio", "contracts", "crm"],
  "allowed_locations": ["paris", "remote france"],
  "allowed_contracts": ["cdi", "cdd"],
  "known_languages": ["french", "english"],
  "submission_score": 70
}
```

### Offer

Each offer records normalized requirements and application friction:

```json
{
  "offer_id": "sample-001",
  "title": "Customer Success Manager",
  "company": "Sample Industries",
  "url": "https://careers.sample.test/jobs/001",
  "location": "Paris",
  "contract": "CDI",
  "required_evidence": ["b2b", "portfolio"],
  "preferred_evidence": ["crm"],
  "required_languages": ["french", "english"],
  "friction": [],
  "active": true
}
```

The repository examples are fictional and use reserved `.test` domains.

## Scoring

The score is intentionally simple and inspectable:

- target-title alignment: up to 40 points;
- verified hard requirements: up to 30 points;
- verified preferred evidence: up to 20 points;
- compatible location: 5 points;
- compatible contract: 5 points.

Hard gates override score. A 100-point offer with an unknown mandatory fact or OTP remains `review_required`.

## Privacy model

Never commit real candidate material to this repository. The included `.gitignore` blocks common local paths and document formats. The `audit` command fails on likely:

- CV/résumé documents;
- `.env` files;
- private candidate profiles;
- application-history exports;
- local key material.

Keep real profiles and histories outside the repository and pass their paths at runtime.

## Architecture

```text
JSON profile + offers + optional JSONL history
                    │
                    ▼
        deterministic policy gates
                    │
                    ▼
      evidence score + explicit reasons
                    │
                    ▼
       ranked machine-readable JSON
                    │
                    ▼
  optional external ATS/browser connector
```

## Roadmap

- connector interface for official ATS adapters;
- freshness and canonical-employer verification;
- evidence provenance per requirement;
- append-only proof ledger;
- response/interview analytics without candidate PII in the public repository.

## License

MIT
