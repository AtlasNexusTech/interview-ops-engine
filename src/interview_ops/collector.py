from __future__ import annotations

import html as html_module
import ipaddress
import json
import re
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .engine import canonical_url, normalize
from .models import Offer

USER_AGENT = "interview-ops-engine/0.1 (+official-job-collector)"
DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 2_000_000
MAX_ERRORS = 50
FUTURE_TOLERANCE = timedelta(days=1)
Resolver = Callable[[str, int], Any]


class FetchError(RuntimeError):
    """A bounded, safe fetch failed."""


def _resolved_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    records = resolver(host, port)
    addresses: list[str] = []
    for record in records:
        if isinstance(record, str):
            addresses.append(record)
        elif isinstance(record, tuple) and len(record) >= 5:
            sockaddr = record[4]
            if isinstance(sockaddr, tuple) and sockaddr:
                addresses.append(str(sockaddr[0]))
    return addresses


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse legacy inet_aton forms (127.1, decimal integer and hexadecimal)."""
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def validate_public_https_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Validate HTTPS syntax and, when requested, every resolved A/AAAA address."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ValueError("only HTTPS source URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise ValueError("credentials in source URLs are forbidden")
    hostname = parts.hostname
    if not hostname:
        raise ValueError("source URL must contain a hostname")
    lowered = hostname.rstrip(".").lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ValueError("localhost source URLs are forbidden")
    try:
        literal = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        literal = _legacy_ipv4(lowered)
    if literal is not None and not literal.is_global:
        raise ValueError("private or non-public IP source URLs are forbidden")
    if literal is None and resolver is not None:
        try:
            addresses = _resolved_addresses(lowered, parts.port or 443, resolver)
        except OSError as exc:
            raise ValueError("source hostname could not be resolved") from exc
        if not addresses:
            raise ValueError("source hostname did not resolve to a public address")
        try:
            if any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise ValueError("private or non-public IP source URLs are forbidden")
        except ValueError as exc:
            if "non-public" in str(exc):
                raise
            raise ValueError("source hostname returned an invalid address") from exc
    return url


def _report_name(kind: str, value: str) -> str:
    if kind != "jsonld":
        return f"{kind}:{value}"
    parts = urlsplit(value)
    safe = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"jsonld:{safe[:200]}"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    kind: str
    company: str
    identifier: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"greenhouse", "lever", "jsonld"}:
            raise ValueError("source type must be greenhouse, lever or jsonld")
        if not isinstance(self.company, str) or not self.company.strip():
            raise ValueError("source company must be a non-empty string")
        if self.kind == "jsonld":
            if self.identifier is not None or self.url is None:
                raise ValueError("jsonld sources require url and no identifier")
            validate_public_https_url(self.url)
        else:
            if self.url is not None or not isinstance(self.identifier, str):
                raise ValueError(f"{self.kind} sources require identifier and no url")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", self.identifier):
                raise ValueError("source identifier contains forbidden characters")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceConfig:
        if not isinstance(value, dict):
            raise ValueError("each source must be an object")
        unknown = set(value) - {"type", "company", "identifier", "url"}
        if unknown:
            raise ValueError("unknown source fields: " + ", ".join(sorted(unknown)))
        return cls(
            kind=value.get("type"), company=value.get("company"),
            identifier=value.get("identifier"), url=value.get("url"),
        )

    @property
    def name(self) -> str:
        return _report_name(self.kind, self.identifier or self.url or "")


class Fetcher(Protocol):
    def fetch(self, url: str, *, timeout: float, max_bytes: int, user_agent: str) -> bytes: ...


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, resolver: Resolver) -> None:
        super().__init__()
        self.resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        validate_public_https_url(newurl, resolver=self.resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SafeHTTPFetcher:
    """Stdlib HTTPS fetcher with DNS/redirect checks and a hard response limit."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver = resolver or socket.getaddrinfo

    def fetch(self, url: str, *, timeout: float, max_bytes: int, user_agent: str) -> bytes:
        validate_public_https_url(url, resolver=self.resolver)
        request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json, text/html"})
        try:
            with build_opener(_SafeRedirectHandler(self.resolver)).open(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > max_bytes:
                    raise FetchError(f"response exceeds {max_bytes} bytes")
                body = response.read(max_bytes + 1)
        except FetchError:
            raise
        except (HTTPError, URLError, OSError, ValueError) as exc:
            raise FetchError(f"request failed ({type(exc).__name__})") from exc
        if len(body) > max_bytes:
            raise FetchError(f"response exceeds {max_bytes} bytes")
        return body


@dataclass(slots=True)
class SourceResult:
    source: str
    fetched: int = 0
    accepted: int = 0
    stale: int = 0
    duplicates: int = 0
    invalid: int = 0
    error: str | None = None


@dataclass(slots=True)
class CollectionReport:
    jobs: list[Offer]
    source_results: list[SourceResult]
    errors: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "jobs": [asdict(job) for job in self.jobs],
            "source_results": [asdict(result) for result in self.source_results],
            "errors": self.errors,
        }


def _endpoint(source: SourceConfig) -> str:
    identifier = quote(source.identifier or "", safe="")
    if source.kind == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true"
    if source.kind == "lever":
        return f"https://api.lever.co/v0/postings/{identifier}?mode=json"
    assert source.url is not None
    return source.url


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(html_module.unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _date(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fallback_url(source: SourceConfig, remote_id: Any) -> str:
    identifier = quote(source.identifier or "", safe="")
    job_id = quote(str(remote_id or "unknown"), safe="")
    if source.kind == "greenhouse":
        return f"https://boards.greenhouse.io/{identifier}/jobs/{job_id}"
    if source.kind == "lever":
        return f"https://jobs.lever.co/{identifier}/{job_id}"
    assert source.url is not None
    return source.url


def _application_url(source: SourceConfig, remote_id: Any, value: Any) -> tuple[str, bool]:
    supplied = isinstance(value, str) and bool(value.strip())
    target = urljoin(source.url or _fallback_url(source, remote_id), value) if supplied else _fallback_url(source, remote_id)
    validate_public_https_url(target)
    host = (urlsplit(target).hostname or "").rstrip(".").lower()
    expected = {
        "greenhouse": {"boards.greenhouse.io", "boards.eu.greenhouse.io"},
        "lever": {"jobs.lever.co"},
    }
    verified = supplied and (source.kind == "jsonld" or host in expected[source.kind])
    return canonical_url(target), verified


def _offer(
    *, source: SourceConfig, remote_id: Any, title: Any, url: Any, location: Any,
    contract: Any = None, published: Any = None, description: Any = None, valid_through: Any = None,
) -> Offer:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("job title is missing")
    target, application_url_verified = _application_url(source, remote_id, url)
    parsed_date = _date(published)
    expiry = _date(valid_through)
    identifier = str(remote_id or canonical_url(target))
    return Offer(
        offer_id=f"{source.kind}:{source.identifier or normalize(source.company)}:{identifier}",
        title=title.strip(), company=source.company.strip(), url=target,
        location=_text(location) or "unknown", contract=_text(contract) or "unknown",
        published_at=parsed_date.isoformat() if parsed_date else None,
        publication_date_known=parsed_date is not None, description=_text(description), source=source.name,
        qualification_complete=False, application_url_verified=application_url_verified,
        valid_through=expiry.isoformat() if expiry else None,
    )


def _parse_items(items: list[Any], factory: Callable[[dict[str, Any]], Offer]) -> tuple[list[Offer], int]:
    offers: list[Offer] = []
    invalid = 0
    for item in items:
        if not isinstance(item, dict):
            invalid += 1
            continue
        try:
            offers.append(factory(item))
        except Exception:  # malformed sibling; deliberately excludes BaseException interrupts
            invalid += 1
    return offers, invalid


def _greenhouse(source: SourceConfig, payload: Any) -> tuple[list[Offer], int]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        raise ValueError("invalid Greenhouse response: jobs must be a list")
    return _parse_items(jobs, lambda item: _offer(
        source=source, remote_id=item.get("id"), title=item.get("title"),
        url=item.get("absolute_url"), location=(item.get("location") or {}).get("name"),
        published=item.get("updated_at"), description=item.get("content"),
    ))


def _lever(source: SourceConfig, payload: Any) -> tuple[list[Offer], int]:
    if not isinstance(payload, list):
        raise ValueError("invalid Lever response: expected a list")

    def convert(item: dict[str, Any]) -> Offer:
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        return _offer(
            source=source, remote_id=item.get("id"), title=item.get("text"),
            url=item.get("hostedUrl") or item.get("applyUrl"), location=categories.get("location"),
            contract=categories.get("commitment"), published=item.get("createdAt"),
            description=item.get("descriptionPlain") or item.get("description"),
        )

    return _parse_items(payload, convert)


class _JSONLDScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.buffer: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mime = dict(attrs).get("type", "") or ""
        if tag.lower() == "script" and mime.split(";", 1)[0].strip().lower() == "application/ld+json":
            self.active = True
            self.buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.active:
            self.scripts.append("".join(self.buffer))
            self.buffer = []
            self.active = False

    def handle_data(self, data: str) -> None:
        if self.active:
            self.buffer.append(data)


def _walk_json(value: Any, depth: int = 0):
    if depth > 12:
        return
    if isinstance(value, list):
        for item in value:
            yield from _walk_json(item, depth + 1)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement", "item", "list"):
            if key in value:
                yield from _walk_json(value[key], depth + 1)


def _location(value: Any, remote: Any) -> str:
    if isinstance(value, list):
        parts = [_location(item, None) for item in value]
        return "; ".join(item for item in parts if item)
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            return ", ".join(
                str(address[key])
                for key in ("addressLocality", "addressRegion", "addressCountry")
                if address.get(key)
            )
    return "Remote" if remote == "TELECOMMUTE" else ""


def _jsonld(source: SourceConfig, body: str) -> tuple[list[Offer], int]:
    parser = _JSONLDScripts()
    parser.feed(body)
    candidates: list[dict[str, Any]] = []
    invalid_scripts = 0
    for script in parser.scripts:
        try:
            document = json.loads(script)
        except json.JSONDecodeError:
            invalid_scripts += 1
            continue
        for item in _walk_json(document):
            item_type = item.get("@type")
            if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
                candidates.append(item)

    def convert(item: dict[str, Any]) -> Offer:
        identifier = item.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value") or identifier.get("name")
        contract = item.get("employmentType")
        if isinstance(contract, list):
            contract = ", ".join(str(part) for part in contract)
        return _offer(
            source=source, remote_id=identifier, title=item.get("title"), url=item.get("url"),
            location=_location(item.get("jobLocation"), item.get("jobLocationType")),
            contract=contract, published=item.get("datePosted"), description=item.get("description"),
            valid_through=item.get("validThrough"),
        )

    offers, invalid = _parse_items(candidates, convert)
    return offers, invalid + invalid_scripts


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, FetchError):
        text = str(exc).splitlines()[0][:200]
        text = re.sub(r"(?i)(token|key|secret|password)\s*=\s*[^&\s]+", r"\1=[redacted]", text)
        return text
    return f"source collection failed ({type(exc).__name__})"


def collect_sources(
    sources: list[SourceConfig], *, fetcher: Fetcher | None = None, max_age_days: int = 30,
    timeout: float = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT, now: datetime | None = None,
) -> CollectionReport:
    if not isinstance(max_age_days, int) or isinstance(max_age_days, bool) or max_age_days < 0:
        raise ValueError("max_age_days must be a non-negative integer")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60 or max_bytes <= 0:
        raise ValueError("timeout must be between 0 and 60 seconds and max_bytes must be positive")
    active_fetcher = fetcher or SafeHTTPFetcher()
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    jobs: list[Offer] = []
    source_results: list[SourceResult] = []
    errors: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_fallbacks: set[tuple[str, str, str]] = set()
    for source in sources:
        result = SourceResult(source=source.name)
        source_results.append(result)
        try:
            endpoint = _endpoint(source)
            validate_public_https_url(endpoint)
            raw = active_fetcher.fetch(endpoint, timeout=timeout, max_bytes=max_bytes, user_agent=user_agent)
            if not isinstance(raw, bytes) or len(raw) > max_bytes:
                raise FetchError("fetcher returned an invalid or oversized response")
            if source.kind == "jsonld":
                found, invalid = _jsonld(source, raw.decode("utf-8"))
            else:
                payload = json.loads(raw.decode("utf-8"))
                found, invalid = _greenhouse(source, payload) if source.kind == "greenhouse" else _lever(source, payload)
            result.fetched = len(found) + invalid
            result.invalid = invalid
            for job in found:
                posted = _date(job.published_at)
                expiry = _date(job.valid_through)
                if (
                    (posted is not None and (clock - posted > timedelta(days=max_age_days) or posted - clock > FUTURE_TOLERANCE))
                    or (expiry is not None and expiry < clock)
                ):
                    result.stale += 1
                    continue
                fallback = (normalize(job.company), normalize(job.title), normalize(job.location))
                if job.url in seen_urls or fallback in seen_fallbacks:
                    result.duplicates += 1
                    continue
                jobs.append(job)
                seen_urls.add(job.url)
                seen_fallbacks.add(fallback)
                result.accepted += 1
        except Exception as exc:  # intentionally excludes KeyboardInterrupt/SystemExit
            message = _safe_error(exc)
            result.error = message
            if len(errors) < MAX_ERRORS:
                errors.append({"source": source.name, "error": message})
    return CollectionReport(jobs=jobs, source_results=source_results, errors=errors)
