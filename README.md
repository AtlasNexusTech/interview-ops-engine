# Interview Ops Engine

[![CI](https://github.com/AtlasNexusTech/interview-ops-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/AtlasNexusTech/interview-ops-engine/actions/workflows/ci.yml)

A deterministic, privacy-first engine for collecting, qualifying, deduplicating and ranking job opportunities **before** any application automation touches an ATS.

## Why

Most job-search automations optimize for application volume. That creates duplicates, weak-fit submissions and unsafe attempts to answer unknown screening questions. Interview Ops Engine uses explicit evidence and hard gates instead:

- rejects excluded titles, inactive listings, incompatible locations and contracts;
- deduplicates canonical URLs and company/title pairs;
- distinguishes verified evidence from missing or unextracted requirements;
- blocks automatic submission when CAPTCHA, OTP, authentication, unknown fields or an unverified application URL appears;
- collects public jobs from official Greenhouse and Lever APIs or explicit JSON-LD pages;
- applies bounded freshness, network and response-size controls;
- isolates failures by source and malformed offer;
- produces transparent scores, reasons and operational states;
- audits a repository tree for likely CVs, secrets and private application histories.

It does **not** bypass CAPTCHA, authentication, robots/access controls or anti-bot systems, and it does not fabricate candidate facts. Collection never submits an application.

## Operational states

- `submission_ready` — all hard requirements are verified, the score passes policy and no human gate is present.
- `shortlisted` — compatible but below the configured submission threshold.
- `review_required` — a required fact is unknown, collector qualification is incomplete, publication date is unknown, application URL is unverified, or a human/security gate exists.
- `duplicate` — the offer already exists in history or appeared earlier in the batch.
- `rejected` — inactive or outside explicit policy.

`auto_submit=true` is emitted only for `submission_ready`. **Collected offers are fail-closed:** current collectors normalize listings but do not reliably extract all requirements or application friction, so they set `qualification_complete=false` and can never become `submission_ready` until a trusted enrichment step verifies and updates them. Legacy/manual offer JSON omitting this field keeps its previous behavior (`true` by default).

## Install and validate

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

python -m pytest -q
python -m ruff check .
PYTHONPATH=src python -m interview_ops.cli audit .
```

## Qualify existing offers

```bash
interview-ops qualify \
  --profile /safe/private/profile.json \
  --offers offers.json \
  --history /safe/private/history.jsonl \
  --output out/ranked.json
```

The history is optional for `qualify`. Keep real profiles and histories outside the repository.

## Collect

Copy and edit `examples/sources.example.json`, then run:

```bash
interview-ops collect \
  --sources sources.json \
  --max-age-days 30 \
  --timeout 10 \
  --max-bytes 2000000 \
  --output out/collected.json
```

Supported sources:

```json
{
  "sources": [
    {"type": "greenhouse", "company": "Example", "identifier": "board_token"},
    {"type": "lever", "company": "Example", "identifier": "site_name"},
    {"type": "jsonld", "company": "Example", "url": "https://careers.example.com/jobs"}
  ]
}
```

- Greenhouse identifiers are inserted only into `boards-api.greenhouse.io` endpoints.
- Lever identifiers are inserted only into `api.lever.co` endpoints.
- JSON-LD URLs must be explicit public HTTPS pages without credentials.
- Unknown source keys and unsafe identifier characters are rejected before fetching.

### Collect and score in one command

```bash
interview-ops collect \
  --sources sources.json \
  --profile /safe/private/profile.json \
  --history /safe/private/history.jsonl \
  --max-age-days 14 \
  --output out/collected-and-ranked.json
```

`--history` requires `--profile`; this and all local input/bound validations happen before network access. A timeout must be finite and in `(0, 60]` seconds. `--max-bytes` is bounded to 10 MB by the CLI.

Exit codes for `collect`:

- `0`: at least one configured source completed, including a valid empty source; partial source failures remain in the JSON report;
- `2`: every configured source failed; the JSON report is still written.

Invalid CLI/input data raises an error and no fetch is attempted.

## Data contracts

### Candidate profile

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
  "active": true,
  "published_at": "2026-07-14T10:00:00+00:00",
  "publication_date_known": true,
  "valid_through": null,
  "source": "manual",
  "description": "Normalized public description",
  "qualification_complete": true,
  "application_url_verified": true
}
```

New metadata fields are optional for backward compatibility. Dates, when present, must be ISO-8601 strings. `publication_date_known=true` requires `published_at`.

### Collection report

```json
{
  "jobs": ["Offer objects as above"],
  "source_results": [
    {
      "source": "lever:site_name",
      "fetched": 3,
      "accepted": 1,
      "stale": 1,
      "duplicates": 0,
      "invalid": 1,
      "error": null
    }
  ],
  "errors": [{"source": "greenhouse:board", "error": "bounded sanitized message"}],
  "ranked": ["Evaluation objects; present only with --profile"]
}
```

`source_results.source` never includes a JSON-LD query or fragment. Error lists and messages are bounded/sanitized. A malformed offer increments `invalid` without discarding valid siblings. Ordinary fetch/parser exceptions fail only their source; `KeyboardInterrupt` and `SystemExit` are deliberately not swallowed.

## Freshness and activity

The collector:

- rejects a known publication date older than `--max-age-days`;
- rejects dates more than one day in the future (small clock-skew tolerance);
- rejects JSON-LD `validThrough` values older than collection time;
- keeps unknown publication dates but marks `publication_date_known=false`;
- leaves currently accepted listings `active=true` and excludes expired/stale ones from `jobs`;
- counts old, future and expired listings in `stale`.

Unknown-date collected offers remain visible for human review but cannot be auto-submitted. JSON-LD supports multiple scripts, MIME parameters such as `application/ld+json; charset=utf-8`, lists, `@graph`, `mainEntity`, `itemListElement`, `item` and `list`, with bounded traversal depth. A parseable page containing no jobs is a successful empty source, not a transport failure.

## Network and URL safeguards

Every real fetch and every redirect is checked before connection:

- HTTPS only; no URL credentials or localhost names;
- direct IPv4/IPv6 literals must be globally routable;
- legacy IPv4 forms such as `127.1`, decimal integers and hexadecimal forms are parsed and rejected when non-global;
- DNS A/AAAA results are resolved through an injectable resolver and every result must be global;
- redirects are revalidated with the same resolver;
- response bytes and timeout are bounded.

Application URLs use the same HTTPS/literal policy. Greenhouse URLs are considered verified only on `boards.greenhouse.io` or `boards.eu.greenhouse.io`; Lever URLs only on `jobs.lever.co`. A missing ATS URL receives a conservative official job-page fallback, never the API endpoint, and `application_url_verified=false`. A public HTTPS URL on an unexpected domain is retained for review rather than silently trusted or discarded.

**Known DNS TOCTOU limitation:** `urllib` performs its own DNS lookup after validation, so an attacker controlling DNS could theoretically change answers between the explicit check and socket connection. The resolver is injected for deterministic tests and all redirects are checked, but this stdlib implementation does not pin the validated IP to the TLS connection. Deploy behind an egress allow-list/proxy or replace the fetcher with an IP-pinning client when consuming adversarial source configuration.

## Scoring

The score is intentionally simple and inspectable:

- target-title alignment: up to 40 points;
- verified hard requirements: up to 30 points;
- verified preferred evidence: up to 20 points;
- compatible location: 5 points;
- compatible contract: 5 points.

Hard gates override score. A 100-point offer with unknown mandatory facts, incomplete collector qualification, unknown collected publication date, an unverified URL or OTP remains `review_required`.

## Privacy model

Never commit real candidate material. The included `.gitignore` blocks common local paths and document formats. The `audit` command fails on likely CV/résumé documents, `.env` files, private profiles, application-history exports and local key material.

The repository examples are fictional and use reserved `.test` domains. Keep real profiles and histories outside the repository and pass their paths at runtime.

## Architecture

```text
explicit public sources ──► bounded collector ──► normalized Offer[]
                                                  │
JSON profile + optional JSONL history ────────────┤
                                                  ▼
                                      deterministic policy gates
                                                  │
                                                  ▼
                                  ranked JSON + explicit reasons
                                                  │
                                                  ▼
                              optional external ATS/browser connector
```

## Roadmap

Completed in the current collector: official Greenhouse/Lever adapters, structured JSON-LD, freshness/expiry filtering, per-source/per-offer isolation, SSRF checks, safe application-URL provenance and fail-closed collector qualification.

Next:

- trusted requirement/friction enrichment with evidence provenance;
- DNS/IP-pinning HTTP transport for hostile multi-tenant deployments;
- canonical-employer verification;
- append-only proof ledger;
- response/interview analytics without candidate PII in the public repository.

## License

MIT
