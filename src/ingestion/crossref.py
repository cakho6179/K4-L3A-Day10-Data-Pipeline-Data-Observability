from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import html
from pathlib import Path
import re
import time
from typing import Any

import requests

from core.config import Settings
from core.utils import normalize_whitespace, read_json, write_json

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "day10-data-observability-lab/0.1 (mailto:lab@example.com)"

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class PaperRecord:
    paper_id: str
    title: str
    summary: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    abs_url: str
    pdf_url: str
    comment: str


def strip_markup(value: str) -> str:
    """Remove JATS/HTML tags, unescape entities and collapse whitespace."""
    return normalize_whitespace(html.unescape(_TAG_RE.sub(" ", value or "")))


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return strip_markup(str(value[0])) if value else ""
    return strip_markup(str(value or ""))


def _date_from_parts(field: Any) -> str:
    """Crossref dates look like {"date-parts": [[2026, 5, 20]]}; missing parts default to 1."""
    if not isinstance(field, dict):
        return ""
    parts = (field.get("date-parts") or [[]])[0] or []
    if not parts or parts[0] is None:
        return ""
    year, month, day = (list(parts) + [1, 1])[:3]
    try:
        return date(int(year), int(month or 1), int(day or 1)).isoformat()
    except (TypeError, ValueError):
        return ""


def _published_date(item: dict[str, Any]) -> str:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        value = _date_from_parts(item.get(key))
        if value:
            return value
    return ""


def _updated_date(item: dict[str, Any], fallback: str) -> str:
    for key in ("indexed", "deposited", "created"):
        field = item.get(key)
        if isinstance(field, dict) and field.get("date-time"):
            return str(field["date-time"])[:10]
    return fallback


def _authors(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for author in item.get("author") or []:
        name = normalize_whitespace(f"{author.get('given', '')} {author.get('family', '')}")
        name = name or normalize_whitespace(str(author.get("name", "")))
        if name:
            names.append(name)
    return names


def _pdf_url(item: dict[str, Any], fallback: str) -> str:
    for link in item.get("link") or []:
        if "pdf" in str(link.get("content-type", "")).lower() and link.get("URL"):
            return str(link["URL"])
    return fallback


def parse_crossref_payload(payload: dict) -> list[PaperRecord]:
    """Parse a Crossref `/works` payload into `PaperRecord`s.

    Records without DOI, title, abstract or a parseable publication date are
    dropped; repeated DOIs keep only their first occurrence.
    """
    items = (payload.get("message") or {}).get("items") or []
    records: list[PaperRecord] = []
    seen: set[str] = set()
    for item in items:
        paper_id = normalize_whitespace(str(item.get("DOI", ""))).lower()
        title = _first_text(item.get("title"))
        summary = strip_markup(str(item.get("abstract", "")))
        published = _published_date(item)
        if not paper_id or not title or not summary or not published or paper_id in seen:
            continue
        seen.add(paper_id)
        subjects = [strip_markup(str(s)) for s in item.get("subject") or [] if str(s).strip()]
        abs_url = str(item.get("URL") or f"https://doi.org/{paper_id}")
        records.append(
            PaperRecord(
                paper_id=paper_id,
                title=title,
                summary=summary,
                authors=_authors(item),
                categories=subjects,
                primary_category=subjects[0] if subjects else "Uncategorized",
                published=published,
                updated=_updated_date(item, published),
                abs_url=abs_url,
                pdf_url=_pdf_url(item, abs_url),
                comment=f"Crossref record {paper_id}",
            )
        )
    return records


def _request_crossref(settings: Settings) -> dict[str, Any]:
    params = {
        "query": settings.source_query,
        "filter": settings.source_filter,
        "rows": settings.max_results,
        "sort": "published",
        "order": "desc",
    }
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                CROSSREF_WORKS_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0**attempt
                last_error = RuntimeError(f"Crossref returned HTTP {response.status_code}")
                time.sleep(min(delay, 30.0))
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(min(2.0**attempt, 30.0))
    raise RuntimeError(f"Crossref API unavailable after {MAX_RETRIES} attempts: {last_error}")


def fetch_source_records(settings: Settings) -> list[PaperRecord]:
    """Load source records and persist both raw artifacts (response + parsed records).

    Offline/dev mode (default) reads the snapshot at `raw_api_response`. Live mode
    (`REFRESH_SOURCE=1`, or no snapshot yet) calls the Crossref API with retry on
    429/5xx and falls back to the snapshot when the API stays unavailable.
    """
    raw_path = settings.paths.raw_api_response
    payload: dict[str, Any] | None = None
    if settings.refresh_source or not raw_path.exists():
        try:
            live_payload = _request_crossref(settings)
            if parse_crossref_payload(live_payload):
                payload = live_payload
                write_json(raw_path, payload)
                print(f"[ingestion] Fetched live Crossref response -> {raw_path}")
            else:
                print("[ingestion] Live response had no usable records; using snapshot.")
        except RuntimeError as exc:
            if not raw_path.exists():
                raise
            print(f"[ingestion] {exc}. Falling back to offline snapshot {raw_path}")
    if payload is None:
        payload = read_json(raw_path)
        print(f"[ingestion] Loaded offline snapshot {raw_path}")
    records = parse_crossref_payload(payload)
    if not records:
        raise RuntimeError(f"No valid Crossref records could be parsed from {raw_path}")
    write_json(settings.paths.raw_records_json, [asdict(record) for record in records])
    return records


def load_raw_records(path: Path) -> list[PaperRecord]:
    """Read the parsed raw snapshot (`crossref_records.json`) into `PaperRecord`s."""
    rows = read_json(Path(path))
    return [
        PaperRecord(
            paper_id=str(row["paper_id"]),
            title=str(row.get("title", "")),
            summary=str(row.get("summary", "")),
            authors=[str(a) for a in row.get("authors") or []],
            categories=[str(c) for c in row.get("categories") or []],
            primary_category=str(row.get("primary_category", "")),
            published=str(row.get("published", "")),
            updated=str(row.get("updated", "")),
            abs_url=str(row.get("abs_url", "")),
            pdf_url=str(row.get("pdf_url", "")),
            comment=str(row.get("comment", "")),
        )
        for row in rows
    ]
