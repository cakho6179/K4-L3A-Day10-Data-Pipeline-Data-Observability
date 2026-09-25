# Phase 1 Report — Baseline Pipeline

_Generated at 2026-09-25T18:40:54.597828+00:00_

## 1. Source and lineage

| Field | Value |
| --- | --- |
| Source API | Crossref REST API |
| Query | agentic retrieval augmented generation large language model |
| Filter | from-pub-date:2026-03-29,has-abstract:true |
| Mode | offline snapshot |
| Raw response | data\raw\crossref_response.json |
| Raw records | 24 (data\raw\crossref_records.json) |
| Clean rows | 24 (data\clean\papers_clean.csv) |
| Dataset fingerprint | 123465407db763f4 |
| Embedding model | sentence-transformers/all-MiniLM-L6-v2 |
| Chroma collection | papers-baseline (24 docs) |
| Test set | 10 questions (data\eval\test_set.json) |
| Top-k | 4 |

## 2. Retrieval and answer quality (baseline)

| Metric | Value |
| --- | --- |
| Retrieval Hit Rate | 1.0000 |
| Mean Token F1 | 1.0000 |
| Judge Accuracy | 1.0000 |
| Mean Judge Score (1-5) | 5 |
| Samples | 10 |
| Judge mode | heuristic-fallback |

### Per question type

| Question type | Hit rate | Token F1 | Judge accuracy |
| --- | --- | --- | --- |
| authors | 1.0000 | 1.0000 | 1.0000 |
| categories | 1.0000 | 1.0000 | 1.0000 |
| date | 1.0000 | 1.0000 | 1.0000 |
| summary | 1.0000 | 1.0000 | 1.0000 |

## 3. Data quality gate (Great Expectations 1.x)

- Engine: `great_expectations 1.23.1` (ephemeral context, pandas source + whole-dataframe batch)
- Suite result: **True** (7/7 expectations passed)

| Expectation | Column | Success | Observed / unexpected |
| --- | --- | --- | --- |
| expect_table_row_count_to_be_between | (table) | True | 24 |
| expect_column_values_to_not_be_null | paper_id | True | 0 |
| expect_column_values_to_be_unique | paper_id | True | 0 |
| expect_column_values_to_not_be_null | title | True | 0 |
| expect_column_value_lengths_to_be_between | title | True | 0 |
| expect_column_values_to_not_be_null | text_for_embedding | True | 0 |
| expect_column_value_lengths_to_be_between | summary | True | 0 |

## 4. Freshness SLA

| Field | Value |
| --- | --- |
| Threshold (days) | 180 |
| Latest published | 2026-07-22 |
| Oldest published | 2026-03-28 |
| Stale rows / total | 1 / 24 |
| Stale ratio (max allowed) | 4.17% (25%) |
| is_fresh | True |

## 5. Verdict

- Quality gate: **PASSED**.
- Freshness: **FRESH** (1/24 rows older than 180 days).
- Baseline hit rate 1.0000, token F1 1.0000: reference numbers for the corruption experiment.
