from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from core.utils import compact_join
from ingestion.crossref import PaperRecord, strip_markup

CLEAN_COLUMNS = [
    "paper_id",
    "title",
    "summary",
    "authors_joined",
    "categories_joined",
    "primary_category",
    "author_count",
    "published",
    "updated",
    "age_days",
    "abs_url",
    "pdf_url",
    "comment",
    "summary_chars",
    "text_for_embedding",
]


def build_text_for_embedding(row: dict[str, Any] | pd.Series) -> str:
    """The 5-part document that is embedded and returned as retrieval context."""
    return "\n".join(
        [
            f"Title: {row['title']}",
            f"Authors: {row['authors_joined']}",
            f"Published: {row['published']}",
            f"Categories: {row['categories_joined']}",
            f"Summary: {row['summary']}",
        ]
    )


def _parse_date(value: str):
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed.date()


def build_clean_dataframe(records: list[PaperRecord], run_date: datetime) -> pd.DataFrame:
    """Turn raw records into the embed-ready dataframe (one row per `paper_id`).

    Rows without id/title/summary or a parseable `published` date are dropped;
    `age_days` is measured against `run_date` for the downstream freshness SLA.
    """
    today = run_date.date()
    rows: list[dict[str, Any]] = []
    for record in records:
        paper_id = strip_markup(record.paper_id).lower()
        title = strip_markup(record.title)
        summary = strip_markup(record.summary)
        published = _parse_date(record.published)
        if not paper_id or not title or not summary or published is None:
            continue
        updated = _parse_date(record.updated) or published
        authors = [strip_markup(a) for a in record.authors]
        categories = [strip_markup(c) for c in record.categories]
        rows.append(
            {
                "paper_id": paper_id,
                "title": title,
                "summary": summary,
                "authors_joined": compact_join(authors),
                "categories_joined": compact_join(categories),
                "primary_category": strip_markup(record.primary_category)
                or (categories[0] if categories else ""),
                "author_count": len([a for a in authors if a]),
                "published": published.isoformat(),
                "updated": updated.isoformat(),
                "age_days": (today - published).days,
                "abs_url": record.abs_url.strip(),
                "pdf_url": record.pdf_url.strip(),
                "comment": strip_markup(record.comment),
            }
        )
    base = [c for c in CLEAN_COLUMNS if c not in {"summary_chars", "text_for_embedding"}]
    df = pd.DataFrame(rows, columns=base)
    df = df.drop_duplicates(subset=["paper_id"], keep="first")
    df["summary_chars"] = df["summary"].str.len().astype(int)
    df["text_for_embedding"] = [build_text_for_embedding(row) for _, row in df.iterrows()]
    df = df.sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    return df[CLEAN_COLUMNS]
