from __future__ import annotations

from typing import Any

import pandas as pd

from core.utils import first_sentence, write_json

MIN_DOCUMENTS = 10
TEST_SET_SIZE = 10
# 10 questions spread over the 4 business question types.
QUESTION_PLAN = [
    "summary", "authors", "date", "categories",
    "summary", "authors", "date", "categories",
    "summary", "authors",
]

QUESTION_TEMPLATES = {
    "summary": "What is the summary of the paper '{title}'?",
    "authors": "Who authored the paper '{title}'?",
    "date": "When was the paper '{title}' published?",
    "categories": "What categories does the paper '{title}' belong to?",
}


def _ground_truth(question_type: str, row: pd.Series) -> str:
    if question_type == "summary":
        return first_sentence(str(row["summary"]))
    if question_type == "authors":
        return str(row["authors_joined"])
    if question_type == "date":
        return str(row["published"])
    return str(row["categories_joined"])


def build_test_set(df: pd.DataFrame, output_path) -> list[dict[str, Any]]:
    """Build a deterministic benchmark from the clean dataframe.

    Papers are picked at evenly spaced positions of the recency-sorted corpus so
    the set covers newest to oldest. Titles with single quotes are skipped
    because the question template quotes the title.
    """
    if len(df) < MIN_DOCUMENTS:
        raise ValueError(
            f"Need at least {MIN_DOCUMENTS} clean documents to build a test set, got {len(df)}."
        )
    candidates = (
        df[~df["title"].astype(str).str.contains("'", regex=False)]
        .sort_values(["published", "paper_id"], ascending=[False, True])
        .reset_index(drop=True)
    )
    if len(candidates) < TEST_SET_SIZE:
        raise ValueError("Not enough papers with quote-free titles to build the test set.")
    step = (len(candidates) - 1) / (TEST_SET_SIZE - 1)
    positions = [round(i * step) for i in range(TEST_SET_SIZE)]
    test_set: list[dict[str, Any]] = []
    for number, (position, question_type) in enumerate(zip(positions, QUESTION_PLAN, strict=True), start=1):
        row = candidates.iloc[position]
        test_set.append(
            {
                "id": f"eval_{number:03d}",
                "question_type": question_type,
                "question": QUESTION_TEMPLATES[question_type].format(title=row["title"]),
                "ground_truth": _ground_truth(question_type, row),
                "ground_truth_doc_ids": [str(row["paper_id"])],
            }
        )
    write_json(output_path, test_set)
    return test_set
