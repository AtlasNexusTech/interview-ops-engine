import json
from datetime import datetime, timezone

import pytest

from interview_ops.collector import (
    FetchError,
    SourceConfig,
    collect_sources,
    validate_public_https_url,
)


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


class FakeFetcher:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch(self, url, *, timeout, max_bytes, user_agent):
        self.calls.append((url, timeout, max_bytes, user_agent))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response.encode()


def test_source_config_validates_shape_and_network_guards():
    source = SourceConfig.from_dict({"type": "greenhouse", "company": "Example", "identifier": "example"})
    assert source.kind == "greenhouse"
    assert source.identifier == "example"

    with pytest.raises(ValueError, match="HTTPS"):
        SourceConfig.from_dict({"type": "jsonld", "company": "Bad", "url": "http://jobs.example.test"})
    for url in (
        "https://localhost/jobs",
        "https://127.0.0.1/jobs",
        "https://10.1.2.3/jobs",
        "https://user:pass@example.test/jobs",
    ):
        with pytest.raises(ValueError):
            validate_public_https_url(url)


def test_collects_greenhouse_normalizes_freshness_and_deduplicates():
    endpoint = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
    payload = {"jobs": [
        {
            "id": 12,
            "title": "Account Manager",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/12?utm_source=x",
            "location": {"name": "Paris"},
            "updated_at": "2026-07-14T10:00:00Z",
            "content": "<p>Manage accounts</p>",
        },
        {
            "id": 13,
            "title": "Old role",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/13",
            "location": {"name": "Paris"},
            "updated_at": "2026-01-01T00:00:00Z",
        },
        {
            "id": 14,
            "title": "Account Manager",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/12",
            "location": {"name": "Paris"},
        },
    ]}
    fetcher = FakeFetcher({endpoint: json.dumps(payload)})

    report = collect_sources(
        [SourceConfig(kind="greenhouse", company="Example", identifier="example")],
        fetcher=fetcher,
        max_age_days=30,
        now=NOW,
    )

    assert len(report.jobs) == 1
    job = report.jobs[0]
    assert job.offer_id == "greenhouse:example:12"
    assert job.url == "https://boards.greenhouse.io/example/jobs/12"
    assert job.location == "Paris"
    assert job.contract == "unknown"
    assert job.published_at == "2026-07-14T10:00:00+00:00"
    assert job.publication_date_known is True
    assert job.description == "Manage accounts"
    assert job.source == "greenhouse:example"
    assert report.source_results[0].fetched == 3
    assert report.source_results[0].accepted == 1
    assert report.source_results[0].stale == 1
    assert report.source_results[0].duplicates == 1
    assert fetcher.calls[0][1:] == (10.0, 2_000_000, "interview-ops-engine/0.1 (+official-job-collector)")


def test_collects_lever_and_keeps_unknown_dates_identifiable():
    endpoint = "https://api.lever.co/v0/postings/example?mode=json"
    payload = [{
        "id": "abc",
        "text": "Customer Success Manager",
        "hostedUrl": "https://jobs.lever.co/example/abc",
        "categories": {"location": "Remote", "commitment": "Full-time"},
        "descriptionPlain": "Help customers",
    }]

    report = collect_sources(
        [SourceConfig(kind="lever", company="Example", identifier="example")],
        fetcher=FakeFetcher({endpoint: json.dumps(payload)}),
        now=NOW,
    )

    assert report.jobs[0].contract == "Full-time"
    assert report.jobs[0].publication_date_known is False
    assert report.jobs[0].published_at is None
    assert report.jobs[0].description == "Help customers"


def test_lever_created_at_milliseconds_is_a_known_publication_date():
    endpoint = "https://api.lever.co/v0/postings/example?mode=json"
    payload = [{
        "id": "dated", "text": "Dated role", "hostedUrl": "https://jobs.lever.co/example/dated",
        "categories": {"location": "Paris"}, "createdAt": 1783987200000,
    }]

    report = collect_sources(
        [SourceConfig(kind="lever", company="Example", identifier="example")],
        fetcher=FakeFetcher({endpoint: json.dumps(payload)}), now=NOW,
    )

    assert report.jobs[0].publication_date_known is True
    assert report.jobs[0].published_at == "2026-07-14T00:00:00+00:00"


def test_collects_jsonld_object_graph_and_lists_with_canonical_application_url():
    url = "https://careers.example.test/jobs"
    html = '''<html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[
      {"@type":"Organization","name":"Ignore"},
      {"@type":"JobPosting","title":"Sales Lead","datePosted":"2026-07-10",
       "employmentType":["FULL_TIME"],"description":"<b>Lead sales</b>",
       "jobLocation":{"address":{"addressLocality":"Lyon","addressCountry":"FR"}},
       "url":"https://careers.example.test/apply/1?utm_campaign=a"},
      [{"@type":"JobPosting","title":"Support Lead","datePosted":"2026-07-11",
        "jobLocationType":"TELECOMMUTE","identifier":{"value":"support-1"}}]
    ]}
    </script></head></html>'''

    report = collect_sources(
        [SourceConfig(kind="jsonld", company="Example", url=url)],
        fetcher=FakeFetcher({url: html}),
        now=NOW,
    )

    assert [job.title for job in report.jobs] == ["Sales Lead", "Support Lead"]
    assert report.jobs[0].url == "https://careers.example.test/apply/1"
    assert report.jobs[0].location == "Lyon, FR"
    assert report.jobs[0].contract == "FULL_TIME"
    assert report.jobs[1].location == "Remote"
    assert report.jobs[1].url == url


def test_source_errors_do_not_cancel_other_sources_and_fallback_dedup_works():
    failed = "https://boards-api.greenhouse.io/v1/boards/broken/jobs?content=true"
    working = "https://api.lever.co/v0/postings/good?mode=json"
    items = [
        {"id": "1", "text": "Role", "hostedUrl": "https://jobs.lever.co/good/1", "categories": {"location": "Paris"}},
        {"id": "2", "text": " role ", "applyUrl": "https://apply.example.test/2", "categories": {"location": "PARIS"}},
    ]
    sources = [
        SourceConfig(kind="greenhouse", company="Broken", identifier="broken"),
        SourceConfig(kind="lever", company="Good", identifier="good"),
    ]

    report = collect_sources(sources, fetcher=FakeFetcher({failed: FetchError("timeout"), working: json.dumps(items)}), now=NOW)

    assert len(report.jobs) == 1
    assert report.source_results[0].error == "timeout"
    assert report.source_results[1].duplicates == 1
    assert report.errors == [{"source": "greenhouse:broken", "error": "timeout"}]


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f000001"])
def test_ssrf_rejects_alternative_ipv4_spellings(host):
    with pytest.raises(ValueError, match="non-public"):
        validate_public_https_url(f"https://{host}/jobs", resolver=lambda _host, _port: [])


def test_ssrf_resolves_every_a_and_aaaa_address_and_rejects_non_global():
    calls = []

    def resolver(host, port):
        calls.append((host, port))
        return ["93.184.216.34", "::1"]

    with pytest.raises(ValueError, match="non-public"):
        validate_public_https_url("https://jobs.example.com/opening", resolver=resolver)
    assert calls == [("jobs.example.com", 443)]


def test_malformed_offer_isolated_and_unexpected_fetch_error_is_sanitized():
    broken = "https://boards-api.greenhouse.io/v1/boards/broken/jobs?content=true"
    working = "https://api.lever.co/v0/postings/good?mode=json"
    report = collect_sources(
        [
            SourceConfig(kind="greenhouse", company="Broken", identifier="broken"),
            SourceConfig(kind="lever", company="Good", identifier="good"),
        ],
        fetcher=FakeFetcher({
            broken: RuntimeError("token=secret\ninternal details"),
            working: json.dumps([
                {"id": "bad", "categories": {}},
                {"id": "ok", "text": "Role", "hostedUrl": "https://jobs.lever.co/good/ok",
                 "categories": {"location": "Paris"}},
            ]),
        }),
        now=NOW,
    )
    assert [job.offer_id for job in report.jobs] == ["lever:good:ok"]
    assert report.source_results[1].invalid == 1
    assert "secret" not in report.errors[0]["error"]
    assert "\n" not in report.errors[0]["error"]


def test_freshness_rejects_future_and_expired_jsonld_but_keeps_unknown_date():
    url = "https://careers.example.com/jobs?tenant=private&token=secret"
    body = '''<script type="application/ld+json; charset=utf-8">{
      "mainEntity":{"itemListElement":[
        {"@type":"JobPosting","title":"Future","datePosted":"2026-07-18","url":"https://careers.example.com/future"},
        {"@type":"JobPosting","title":"Expired","datePosted":"2026-07-10","validThrough":"2026-07-14","url":"https://careers.example.com/expired"},
        {"@type":"JobPosting","title":"Unknown date","url":"https://careers.example.com/current"}
      ]}}
    </script>'''
    report = collect_sources(
        [SourceConfig(kind="jsonld", company="Example", url=url)],
        fetcher=FakeFetcher({url: body}), now=NOW,
    )
    assert [job.title for job in report.jobs] == ["Unknown date"]
    assert report.source_results[0].stale == 2
    assert "token" not in report.source_results[0].source
    assert report.source_results[0].error is None


def test_ats_application_domains_are_verified_and_api_endpoint_is_never_job_url():
    endpoint = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
    payload = {"jobs": [
        {"id": 1, "title": "Missing URL", "location": {"name": "Paris"}},
        {"id": 2, "title": "Foreign URL", "absolute_url": "https://careers.example.com/2", "location": {"name": "Paris"}},
    ]}
    report = collect_sources(
        [SourceConfig(kind="greenhouse", company="Example", identifier="example")],
        fetcher=FakeFetcher({endpoint: json.dumps(payload)}), now=NOW,
    )
    assert all("boards-api.greenhouse.io" not in job.url for job in report.jobs)
    assert report.jobs[0].url == "https://boards.greenhouse.io/example/jobs/1"
    assert report.jobs[0].application_url_verified is False
    assert report.jobs[1].url == "https://careers.example.com/2"
    assert report.jobs[1].application_url_verified is False
