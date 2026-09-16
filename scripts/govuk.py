"""Bounded HTTP access to GOV.UK publication pages and their attachments.

Every source in this repository is a GOV.UK page exposing the same three
things: downloadable attachments whose URLs carry a content hash that changes
on every republication, an official change history of publication timestamps,
and the ordinary asset CDN headers. Duplicating the host allowlist, retry
budget and download ceiling per source file would put the network policy in two
places and let them drift. This module is the single justified shared module in
this repository; source-specific parsing stays in the `extract_*.py` files.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from scripts.config import (
    BACKOFF_FACTOR,
    MAX_DOWNLOAD_BYTES,
    MAX_RETRIES,
    MAX_RETRY_DELAY,
    RATE_LIMIT_BACKOFF,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from scripts.snapshots import Snapshot
from scripts.time_series import Observation

logger = logging.getLogger(__name__)

# Only the GOV.UK publication site and its asset CDN. An attachment href read
# out of a fetched page is source-controlled input, so it is re-checked against
# this allowlist before it is ever requested.
ALLOWED_HOSTS = frozenset({"www.gov.uk", "assets.publishing.service.gov.uk"})

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RATE_LIMITED_STATUS = 429
# Headers describing the wire body rather than the decoded one. They must not
# travel with decoded bytes, or the body would look doubly encoded.
_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})

# GOV.UK renders the change history with two different class prefixes depending
# on the page template ("publication" vs "statistical data set"), but both carry
# a machine-readable `datetime` on a `...change-date` time element.
_CHANGE_DATE_PATTERN = re.compile(
    r'<time[^>]*class="[^"]*change-date[^"]*"[^>]*datetime="([^"]+)"', re.IGNORECASE
)


def build_client() -> httpx.Client:
    """Build the single managed HTTP client used by one collection call."""
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        trust_env=True,
        follow_redirects=False,
    )


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Return the bounded wait before the next attempt, honouring Retry-After."""
    delay = float(BACKOFF_FACTOR**attempt)
    if response is not None and response.status_code == RATE_LIMITED_STATUS:
        delay = max(delay, RATE_LIMIT_BACKOFF * (attempt + 1))
        header = response.headers.get("retry-after", "").strip()
        if header.isdigit():
            delay = max(delay, float(header))
    return min(delay, MAX_RETRY_DELAY)


def _bounded_body(response: httpx.Response, url: str) -> bytes:
    """Read a streamed body, refusing an implausibly large download."""
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"GOV.UK declared {declared} bytes for {url}, above the download limit")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Response for {url} exceeded the {MAX_DOWNLOAD_BYTES} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def http_get(client: httpx.Client, url: str) -> httpx.Response:
    """Request an allowlisted GOV.UK URL with bounded exponential-backoff retries.

    Every request in this repository goes through here, so the host allowlist,
    redirect policy, retry budget and download ceiling are enforced in one place.
    """
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing non-GOV.UK URL: {url}")
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        response = None
        try:
            with client.stream("GET", url) as streamed:
                response = streamed
                if streamed.status_code not in RETRYABLE_STATUSES:
                    streamed.raise_for_status()
                    body = _bounded_body(streamed, url)
                    return httpx.Response(
                        streamed.status_code,
                        headers=[
                            (name, value)
                            for name, value in streamed.headers.multi_items()
                            if name.lower() not in _TRANSFER_HEADERS
                        ],
                        content=body,
                        request=streamed.request,
                    )
                streamed.read()
                last_error = httpx.HTTPStatusError(
                    f"retryable status {streamed.status_code}",
                    request=streamed.request,
                    response=streamed,
                )
        except httpx.TransportError as exc:
            response = None
            last_error = exc
        if attempt < MAX_RETRIES:
            delay = _retry_delay(attempt, response)
            logger.warning(
                "GOV.UK request failed; retrying in %.1fs (%d/%d)", delay, attempt + 1, MAX_RETRIES
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def fetch_page(client: httpx.Client, url: str) -> str:
    """Return the decoded HTML of a GOV.UK publication page."""
    logger.info("Fetching GOV.UK page %s", url)
    return http_get(client, url).text


def release_timestamps(page_html: str) -> list[datetime]:
    """Return every official publication timestamp in the page change history.

    These are the dates GOV.UK itself stamps on each update of the page, so an
    observation attributed to one of them carries `official_timestamp` rather
    than a guess. The history is not guaranteed to reach back to the first
    observation, and callers must handle the uncovered tail explicitly.
    """
    stamps = set()
    for raw in _CHANGE_DATE_PATTERN.findall(page_html):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("Unparseable GOV.UK change-history timestamp %r; skipping", raw)
            continue
        # GOV.UK stamps these in UTC, normally with a trailing "Z". A future
        # template change that drops the offset must not produce a naive
        # instant that silently compares against aware ones.
        stamps.add(parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC))
    return sorted(stamps)


def find_attachment(page_html: str, page_url: str, pattern: str) -> str:
    """Return the one attachment URL on the page whose href matches ``pattern``.

    Attachment URLs embed a content hash that changes on every republication, so
    they are discovered from the live page instead of being pinned in code. An
    ambiguous or absent match fails loudly: silently taking the first of several
    links is how a collector starts ingesting the wrong file after a layout
    change.
    """
    matches = {
        urljoin(page_url, html.unescape(href))
        for href in re.findall(r'href="([^"]+)"', page_html)
        if re.search(pattern, href, re.IGNORECASE)
    }
    # The CSV preview route serves an HTML viewer for the same file; only the
    # asset CDN copy is the machine-readable artifact.
    matches = {url for url in matches if urlparse(url).hostname != "www.gov.uk"}
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one attachment matching {pattern!r} on {page_url}, "
            f"found {len(matches)}: {sorted(matches)}. The page layout changed and the "
            "attachment must be re-verified against the official source."
        )
    return matches.pop()


def download(client: httpx.Client, url: str) -> tuple[bytes, str, str | None, str | None]:
    """Download one attachment, returning ``(body, sha256, etag, last_modified)``."""
    logger.info("Downloading %s", url)
    response = http_get(client, url)
    body = response.content
    digest = hashlib.sha256(body).hexdigest()
    logger.info("Downloaded %d bytes from %s (sha256 %s)", len(body), url, digest[:12])
    return (
        body,
        digest,
        response.headers.get("etag"),
        response.headers.get("last-modified"),
    )


@dataclass(frozen=True)
class SourceData:
    """Everything one GOV.UK source yielded in a single collection call.

    This is the contract each ``extract_*.py`` returns and ``main.py`` consumes.
    It is a plain data carrier, not a base class: a source module builds one and
    nothing inherits from anything.
    """

    source_id: str
    source_url: str
    # Descriptive fields per series, consumed by scripts/metadata.py.
    catalog: dict[str, dict[str, Any]]
    observations: list[Observation]
    # Official publication timestamps from the page change history.
    releases: list[datetime]
    snapshots: list[Snapshot]
    # Release-attribution window for this source's schedule, in days after the
    # reference period. See scripts/availability.attribute_release.
    min_lag_days: int
    max_lag_days: int
    # Observed release rule used only where the change history does not reach.
    # None means the source supports no defensible inference.
    inferred_lag_days: int | None
    last_publish_date: date | None
