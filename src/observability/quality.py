from __future__ import annotations

from typing import Any
import logging

import great_expectations as gx
from great_expectations.data_context.types.base import ProgressBarsConfig
import pandas as pd

from core.config import Settings
from core.utils import now_utc, write_json

MIN_ROWS = 5
MAX_ROWS = 5000
MIN_SUMMARY_CHARS = 30
MAX_SUMMARY_CHARS = 20000
MIN_TITLE_CHARS = 8
MAX_STALE_RATIO = 0.25
REQUIRED_NOT_NULL_COLUMNS = ("paper_id", "title", "text_for_embedding")

logging.getLogger("great_expectations").setLevel(logging.ERROR)


def _build_expectations() -> list[Any]:
    checks: list[Any] = [
        gx.expectations.ExpectTableRowCountToBeBetween(min_value=MIN_ROWS, max_value=MAX_ROWS),
    ]
    checks += [
        gx.expectations.ExpectColumnValuesToNotBeNull(column=column)
        for column in REQUIRED_NOT_NULL_COLUMNS
    ]
    checks += [
        gx.expectations.ExpectColumnValuesToBeUnique(column="paper_id"),
        gx.expectations.ExpectColumnValueLengthsToBeBetween(
            column="summary", min_value=MIN_SUMMARY_CHARS, max_value=MAX_SUMMARY_CHARS
        ),
        gx.expectations.ExpectColumnValueLengthsToBeBetween(
            column="title", min_value=MIN_TITLE_CHARS
        ),
    ]
    return checks


def _summarize_result(result: Any) -> dict[str, Any]:
    config = result.expectation_config
    kwargs = {k: v for k, v in dict(config.kwargs).items() if k != "batch_id"}
    details = result.result or {}
    return {
        "expectation": config.type,
        "column": kwargs.get("column"),
        "kwargs": kwargs,
        "success": bool(result.success),
        "observed_value": details.get("observed_value"),
        "unexpected_count": details.get("unexpected_count"),
        "unexpected_percent": details.get("unexpected_percent"),
        "partial_unexpected_list": [str(v) for v in (details.get("partial_unexpected_list") or [])][:10],
    }


def _freshness_payload(df: pd.DataFrame, settings: Settings) -> dict[str, Any]:
    threshold = settings.freshness_threshold_days
    total = int(len(df))
    if total:
        published = pd.to_datetime(df["published"], errors="coerce")
        ages = pd.to_numeric(df["age_days"], errors="coerce")
        stale_mask = ages > threshold
        stale_rows = int(stale_mask.sum())
    else:
        published = pd.Series(dtype="datetime64[ns]")
        stale_rows = 0
    stale_ratio = stale_rows / total if total else 1.0
    has_dates = total > 0 and published.notna().any()
    has_ages = total > 0 and ages.notna().any()
    return {
        "checked_at": now_utc().isoformat(),
        "threshold_days": threshold,
        "max_stale_ratio": MAX_STALE_RATIO,
        "latest_published": published.max().date().isoformat() if has_dates else None,
        "oldest_published": published.min().date().isoformat() if has_dates else None,
        "latest_age_days": int(ages.min()) if has_ages else None,
        "median_age_days": float(ages.median()) if has_ages else None,
        "stale_rows": stale_rows,
        "total_rows": total,
        "stale_ratio": round(stale_ratio, 4),
        "stale_paper_ids": sorted(df.loc[stale_mask, "paper_id"].astype(str).unique().tolist())
        if total
        else [],
        "is_fresh": stale_ratio <= MAX_STALE_RATIO,
    }


def run_data_quality_checks(df: pd.DataFrame, settings: Settings, report_name: str) -> dict[str, Any]:
    """Quality gate on the GX 1.x fluent API (ephemeral context, whole-dataframe batch)."""
    context = gx.get_context(mode="ephemeral")
    context.variables.progress_bars = ProgressBarsConfig(globally=False)
    data_source = context.data_sources.add_pandas(name="papers_source")
    data_asset = data_source.add_dataframe_asset(name="papers_asset")
    batch_def = data_asset.add_batch_definition_whole_dataframe("papers_batch")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})

    suite = context.suites.add(gx.ExpectationSuite(name=f"papers_{report_name}_suite"))
    for expectation in _build_expectations():
        suite.add_expectation(expectation)
    validation = batch.validate(suite)

    results = [_summarize_result(r) for r in validation.results]
    freshness = _freshness_payload(df, settings)
    report = {
        "report_name": report_name,
        "engine": f"great_expectations {gx.__version__}",
        "validated_at": now_utc().isoformat(),
        "row_count": int(len(df)),
        "success": bool(validation.success),
        "evaluated_expectations": len(results),
        "successful_expectations": sum(1 for item in results if item["success"]),
        "failed_expectations": [
            item["expectation"] + (f"({item['column']})" if item["column"] else "")
            for item in results
            if not item["success"]
        ],
        "expectations": results,
        "freshness": freshness,
        "gate_passed": bool(validation.success) and freshness["is_fresh"],
    }
    write_json(settings.paths.quality_dir / f"{report_name}_quality_report.json", report)
    return report


def build_freshness_report(df: pd.DataFrame, settings: Settings, report_path) -> dict[str, Any]:
    """Freshness SLA report: stale when >25% of rows exceed the age threshold."""
    payload = _freshness_payload(df, settings)
    write_json(report_path, payload)
    return payload
