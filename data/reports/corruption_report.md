# Corruption Report — Baseline vs Corrupted vs Repaired

_Generated at 2026-09-25T18:41:50.339399+00:00_ — all three states evaluated on the same `data/eval/test_set.json`.

## 1. Headline metrics (3 states)

| Metric | Baseline | Corrupted | Repaired | Δ Corrupted | Δ Repaired |
| --- | --- | --- | --- | --- | --- |
| Retrieval Hit Rate | 1.0000 | 0.8000 | 1.0000 | -0.2000 | +0.0000 |
| Mean Token F1 | 1.0000 | 0.6568 | 1.0000 | -0.3432 | +0.0000 |
| Judge Accuracy | 1.0000 | 0.7000 | 1.0000 | -0.3000 | +0.0000 |
| Mean Judge Score (1-5) | 5 | 3.4000 | 5 | -1.6000 | +0.0000 |
| Samples | 10 | 10 | 10 | - | - |

### Token F1 by question type

| Question type | Baseline | Corrupted | Repaired |
| --- | --- | --- | --- |
| authors | 1.0000 | 0.6667 | 1.0000 |
| categories | 1.0000 | 1.0000 | 1.0000 |
| date | 1.0000 | 0.5000 | 1.0000 |
| summary | 1.0000 | 0.5228 | 1.0000 |

### Retrieval hit rate by question type

| Question type | Baseline | Corrupted | Repaired |
| --- | --- | --- | --- |
| authors | 1.0000 | 0.6667 | 1.0000 |
| categories | 1.0000 | 1.0000 | 1.0000 |
| date | 1.0000 | 1.0000 | 1.0000 |
| summary | 1.0000 | 0.6667 | 1.0000 |

## 2. Observability signals

| Signal | Baseline | Corrupted | Repaired |
| --- | --- | --- | --- |
| Rows validated | 24 | 22 | 24 |
| GX suite success | True | False | True |
| Expectations passed | 7/7 | 4/7 | 7/7 |
| Latest published | 2026-07-22 | 2026-06-12 | 2026-07-22 |
| Stale ratio | 0.0417 | 0.3182 | 0.0417 |
| is_fresh | True | False | True |

### Expectation-level detail

| Expectation | Column | Corrupted success | Corrupted unexpected | Repaired success |
| --- | --- | --- | --- | --- |
| expect_table_row_count_to_be_between | (table) | True | - | True |
| expect_column_values_to_not_be_null | paper_id | True | 0 | True |
| expect_column_values_to_be_unique | paper_id | False | 6 | True |
| expect_column_values_to_not_be_null | title | True | 0 | True |
| expect_column_value_lengths_to_be_between | title | False | 4 | True |
| expect_column_values_to_not_be_null | text_for_embedding | True | 0 | True |
| expect_column_value_lengths_to_be_between | summary | False | 4 | True |

## 3. Injected corruptions

Seed `42` — 24 clean rows -> 22 corrupted rows.

| # | Corruption | Rows | Description |
| --- | --- | --- | --- |
| 1 | drop_latest_records | 5 | Dropped the 5 newest records (20%). |
| 2 | blank_summary | 3 | Replaced the abstract with an empty string. |
| 3 | inject_noise | 3 | Inserted garbage tokens into the abstract. |
| 4 | truncate_title | 3 | Truncated the title to 6 characters. |
| 5 | stale_date | 6 | Moved the published date 365 days into the past. |
| 6 | duplicate_rows | 3 | Appended exact copies of existing rows. |

## 4. Analysis

- **Retrieval Hit Rate**: 1.0000 -> 0.8000 (-20.0% relative) -> 1.0000 after repair (fully recovered).
- **Mean Token F1**: 1.0000 -> 0.6568 (-34.3% relative) -> 1.0000 after repair (fully recovered).
- **Judge Accuracy**: 1.0000 -> 0.7000 (-30.0% relative) -> 1.0000 after repair (fully recovered).
- **Mean Judge Score (1-5)**: 5.0000 -> 3.4000 (-32.0% relative) -> 5.0000 after repair (fully recovered).
- Largest token-F1 drop by question type: `date` (-0.5000).
- GX gate flagged the corrupted batch with 3 failed expectation(s): `expect_column_values_to_be_unique(paper_id)`, `expect_column_value_lengths_to_be_between(title)`, `expect_column_value_lengths_to_be_between(summary)`.
- Freshness on corrupted data: stale ratio 31.82% -> `is_fresh=False`.
- Repaired data re-passed the gate (`success=True`, `is_fresh=True`).
- Idempotency: repaired fingerprint `123465407db7` matches baseline `123465407db7`; second rebuild gave the same fingerprint.

### Why this is a silent failure

On the corrupted index the QA path answered 10/10 questions with no error, yet 5 answers were wrong or degraded. Only the GX gate and freshness SLA signalled a problem.

| ID | Type | Hit | Ground truth | Corrupted answer | Token F1 |
| --- | --- | --- | --- | --- | --- |
| eval_001 | summary | False | Static benchmarks fail to capture domain drift in enterprise knowledge | An extended empirical study on tatic benchmarks fail to capture domain | 0.7407 |
| eval_002 | authors | False | Kien Duong, Vy Ly | Anh Tran, Quoc Pham | 0.0000 |
| eval_005 | summary | True | An extended empirical study on single LLM is susceptible to confirmati | ??!! | 0.0000 |
| eval_007 | date | True | 2026-06-03 | 2025-06-03 | 0.0000 |
| eval_009 | summary | True | Connecting LLMs directly to raw warehouse tables often results in sche | 0xDEADBEEF �� $$^&* Connecting LLMs lorem~~ directly �� to raw warehou | 0.8276 |

### How the repair works

Repair never patches the corrupted table. It rebuilds the clean dataset from the immutable raw snapshot with the same cleaning code, re-validates it with the GX gate and re-indexes it into a separate `papers-repaired` collection. Deterministic input plus deterministic transformation means any number of repair runs yield the same dataset.
