from __future__ import annotations

from datetime import date, timedelta
import math
import random
from typing import Any

import pandas as pd

from core.utils import now_utc, write_json
from ingestion.cleaning import CLEAN_COLUMNS, build_text_for_embedding

DEFAULT_SEED = 42
DROP_LATEST_FRACTION = 0.20
BLANK_SUMMARY_FRACTION = 0.15
NOISE_FRACTION = 0.15
TRUNCATE_TITLE_FRACTION = 0.15
STALE_DATE_FRACTION = 0.30
DUPLICATE_FRACTION = 0.15
TRUNCATED_TITLE_CHARS = 6
STALE_SHIFT_DAYS = 365
NOISE_TOKENS = ["#@!%", "$$^&*", "��", "lorem~~", "0xDEADBEEF", "&&||", "??!!", "<<br>>"]


def _count(fraction: float, total: int) -> int:
    return min(total, max(1, math.ceil(total * fraction))) if total else 0


def _noisy_text(text: str, rng: random.Random) -> str:
    words = text.split()
    noisy: list[str] = [rng.choice(NOISE_TOKENS) for _ in range(3)]
    for word in words:
        noisy.append(word)
        if rng.random() < 0.35:
            noisy.append(rng.choice(NOISE_TOKENS))
    return " ".join(noisy)


def corrupt_clean_dataframe(df: pd.DataFrame, output_log_path, seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Apply 6 seeded corruptions to a clean dataframe and log each one.

    Order: drop latest, blank summary, inject noise, truncate title, stale date,
    duplicate rows. Scenarios 2-5 hit disjoint rows so each effect stays
    attributable; derived columns are rebuilt so corrupted fields reach the index.
    """
    rng = random.Random(seed)
    corrupted = df.copy().sort_values(["published", "paper_id"], ascending=[False, True]).reset_index(drop=True)
    input_rows = len(corrupted)
    log: list[dict[str, Any]] = []

    drop_n = _count(DROP_LATEST_FRACTION, len(corrupted))
    dropped = corrupted.head(drop_n)
    corrupted = corrupted.iloc[drop_n:].reset_index(drop=True)
    log.append(
        {
            "corruption_type": "drop_latest_records",
            "description": f"Dropped the {drop_n} newest records ({DROP_LATEST_FRACTION:.0%}).",
            "affected_rows": drop_n,
            "paper_ids": dropped["paper_id"].tolist(),
            "details": {"dropped_published": dropped["published"].tolist()},
        }
    )

    order = list(corrupted.index)
    rng.shuffle(order)
    cursor = 0

    def take(fraction: float) -> list[int]:
        nonlocal cursor
        picked = order[cursor : cursor + _count(fraction, len(corrupted))]
        cursor += len(picked)
        return picked

    rows = take(BLANK_SUMMARY_FRACTION)
    corrupted.loc[rows, "summary"] = ""
    log.append(
        {
            "corruption_type": "blank_summary",
            "description": "Replaced the abstract with an empty string.",
            "affected_rows": len(rows),
            "paper_ids": corrupted.loc[rows, "paper_id"].tolist(),
        }
    )

    rows = take(NOISE_FRACTION)
    for row in rows:
        corrupted.at[row, "summary"] = _noisy_text(str(corrupted.at[row, "summary"]), rng)
    log.append(
        {
            "corruption_type": "inject_noise",
            "description": "Inserted garbage tokens into the abstract.",
            "affected_rows": len(rows),
            "paper_ids": corrupted.loc[rows, "paper_id"].tolist(),
            "details": {"noise_tokens": NOISE_TOKENS},
        }
    )

    rows = take(TRUNCATE_TITLE_FRACTION)
    before = corrupted.loc[rows, "title"].tolist()
    corrupted.loc[rows, "title"] = corrupted.loc[rows, "title"].str.slice(0, TRUNCATED_TITLE_CHARS).str.strip()
    log.append(
        {
            "corruption_type": "truncate_title",
            "description": f"Truncated the title to {TRUNCATED_TITLE_CHARS} characters.",
            "affected_rows": len(rows),
            "paper_ids": corrupted.loc[rows, "paper_id"].tolist(),
            "details": {
                "examples": [
                    {"before": b, "after": a}
                    for b, a in zip(before, corrupted.loc[rows, "title"].tolist(), strict=True)
                ]
            },
        }
    )

    rows = take(STALE_DATE_FRACTION)
    for row in rows:
        shifted = date.fromisoformat(str(corrupted.at[row, "published"])) - timedelta(days=STALE_SHIFT_DAYS)
        corrupted.at[row, "published"] = shifted.isoformat()
        corrupted.at[row, "updated"] = shifted.isoformat()
        corrupted.at[row, "age_days"] = int(corrupted.at[row, "age_days"]) + STALE_SHIFT_DAYS
    log.append(
        {
            "corruption_type": "stale_date",
            "description": f"Moved the published date {STALE_SHIFT_DAYS} days into the past.",
            "affected_rows": len(rows),
            "paper_ids": corrupted.loc[rows, "paper_id"].tolist(),
        }
    )

    rows = sorted(rng.sample(list(corrupted.index), _count(DUPLICATE_FRACTION, len(corrupted))))
    corrupted["summary_chars"] = corrupted["summary"].str.len().astype(int)
    corrupted["text_for_embedding"] = [build_text_for_embedding(r) for _, r in corrupted.iterrows()]
    duplicates = corrupted.loc[rows].copy()
    log.append(
        {
            "corruption_type": "duplicate_rows",
            "description": "Appended exact copies of existing rows.",
            "affected_rows": len(rows),
            "paper_ids": duplicates["paper_id"].tolist(),
        }
    )
    corrupted = pd.concat([corrupted, duplicates], ignore_index=True)[CLEAN_COLUMNS]

    write_json(
        output_log_path,
        {
            "created_at": now_utc().isoformat(),
            "seed": seed,
            "input_rows": input_rows,
            "output_rows": int(len(corrupted)),
            "corruption_count": len(log),
            "corruptions": log,
        },
    )
    return corrupted
