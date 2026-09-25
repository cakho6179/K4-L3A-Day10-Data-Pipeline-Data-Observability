from __future__ import annotations

import hashlib
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd

from core.config import Settings, load_settings
from core.utils import now_utc, read_json, write_csv, write_json
from evaluation.metrics import evaluate_pipeline
from evaluation.testset import build_test_set
from ingestion.cleaning import build_clean_dataframe
from ingestion.crossref import fetch_source_records
from observability.quality import build_freshness_report, run_data_quality_checks
from observability.reporting import generate_phase1_report
from retrieval.index import LocalEmbeddingIndex

FINGERPRINT_COLUMNS = ["paper_id", "title", "summary", "published", "text_for_embedding"]
DEMO_QUESTIONS = 2


class QualityGateError(RuntimeError):
    pass


def save_dataframe(df: pd.DataFrame, csv_path: Path, json_path: Path) -> None:
    write_csv(df, csv_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(json_path, orient="records", indent=2, force_ascii=False)


def load_dataframe(json_path: Path) -> pd.DataFrame:
    return pd.read_json(json_path, orient="records", convert_dates=False, dtype={"paper_id": str, "published": str, "updated": str})


def dataset_fingerprint(df: pd.DataFrame) -> str:
    """Order-independent hash of the content that reaches the vector store."""
    rows = df[FINGERPRINT_COLUMNS].astype(str).sort_values(FINGERPRINT_COLUMNS).to_csv(index=False)
    return hashlib.sha256(rows.encode("utf-8")).hexdigest()


def load_or_build_test_set(df: pd.DataFrame, settings: Settings) -> list[dict[str, Any]]:
    """Keep the benchmark fixed across runs; rebuild only on request or drift."""
    path = settings.paths.eval_testset
    if path.exists() and not settings.refresh_test_set:
        test_set = read_json(path)
        known_ids = set(df["paper_id"])
        if test_set and all(doc_id in known_ids for item in test_set for doc_id in item["ground_truth_doc_ids"]):
            print(f"[phase1] Reusing fixed test set {path} ({len(test_set)} questions)")
            return test_set
        print("[phase1] Existing test set references unknown papers; rebuilding it.")
    test_set = build_test_set(df, path)
    print(f"[phase1] Built test set {path} ({len(test_set)} questions)")
    return test_set


def enrich_metrics_with_breakdown(metrics_path: Path, answers_path: Path) -> dict[str, Any]:
    """Add per-question-type breakdown and judge mode to a saved metrics file."""
    metrics = read_json(metrics_path)
    answers = read_json(answers_path)
    by_type: dict[str, dict[str, float]] = {}
    for question_type in sorted({a["question_type"] for a in answers}):
        group = [a for a in answers if a["question_type"] == question_type]
        by_type[question_type] = {
            "samples": len(group),
            "retrieval_hit_rate": mean(1.0 if a["retrieval_hit"] else 0.0 for a in group),
            "mean_token_f1": mean(a["token_f1"] for a in group),
            "judge_accuracy": mean(1.0 if a["judge"]["correct"] else 0.0 for a in group),
        }
    metrics["by_question_type"] = by_type
    metrics["judge_mode"] = "heuristic-fallback" if any(
        "Fallback" in a["judge"].get("reasoning", "") for a in answers
    ) else "llm-judge"
    write_json(metrics_path, metrics)
    return metrics


def _run_agent_demo(settings: Settings, index: LocalEmbeddingIndex, test_set: list[dict[str, Any]]) -> None:
    from retrieval.agent import build_agent, run_agent_question

    payload: dict[str, Any] = {"llm_provider": settings.llm_provider, "model": settings.model_name}
    try:
        agent = build_agent(settings, index)
        payload["answers"] = [
            {"question": item["question"], "ground_truth": item["ground_truth"],
             "agent_answer": run_agent_question(agent, item["question"])}
            for item in test_set[:DEMO_QUESTIONS]
        ]
        print(f"[phase1] Agent demo answered {DEMO_QUESTIONS} questions -> {settings.paths.demo_answers}")
    except Exception as exc:
        payload["skipped"] = f"{type(exc).__name__}: {exc}"
        print(f"[phase1] Agent demo skipped ({payload['skipped']})")
    write_json(settings.paths.demo_answers, payload)


def main() -> None:
    settings = load_settings()
    paths = settings.paths
    run_date = now_utc()
    print(f"[phase1] Run date {run_date.date().isoformat()} | provider={settings.llm_provider} | refresh_source={settings.refresh_source}")

    records = fetch_source_records(settings)
    print(f"[phase1] Raw records: {len(records)} -> {paths.raw_records_json}")

    clean_df = build_clean_dataframe(records, run_date)
    save_dataframe(clean_df, paths.clean_csv, paths.clean_json)
    print(f"[phase1] Clean rows: {len(clean_df)} -> {paths.clean_csv}")

    quality = run_data_quality_checks(clean_df, settings, "baseline")
    freshness = build_freshness_report(clean_df, settings, paths.freshness_report)
    print(
        f"[phase1] GX success={quality['success']} "
        f"({quality['successful_expectations']}/{quality['evaluated_expectations']}) | is_fresh={freshness['is_fresh']} "
        f"(stale {freshness['stale_rows']}/{freshness['total_rows']})"
    )
    if not quality["success"]:
        raise QualityGateError(f"Quality gate failed, refusing to index: {quality['failed_expectations']}")
    if not freshness["is_fresh"]:
        print("[phase1] WARNING: freshness SLA violated — corpus needs a refresh (REFRESH_SOURCE=1).")

    index = LocalEmbeddingIndex.build(clean_df, settings, paths.embeddings_json)
    print(f"[phase1] Chroma collection '{index.collection_name}' indexed {index.collection.count()} documents")

    test_set = load_or_build_test_set(clean_df, settings)
    bundle = evaluate_pipeline(settings, index, paths.eval_testset, paths.baseline_metrics, paths.baseline_answers)
    metrics = enrich_metrics_with_breakdown(paths.baseline_metrics, paths.baseline_answers)
    print(
        f"[phase1] Baseline hit_rate={metrics['retrieval_hit_rate']:.4f} token_f1={metrics['mean_token_f1']:.4f} "
        f"judge_accuracy={metrics['judge_accuracy']:.4f} (judge={metrics['judge_mode']})"
    )

    source_summary = {
        "Source API": settings.source_api,
        "Query": settings.source_query,
        "Filter": settings.source_filter,
        "Mode": "live API" if settings.refresh_source else "offline snapshot",
        "Raw response": str(paths.raw_api_response.relative_to(paths.project_dir)),
        "Raw records": f"{len(records)} ({paths.raw_records_json.relative_to(paths.project_dir)})",
        "Clean rows": f"{len(clean_df)} ({paths.clean_csv.relative_to(paths.project_dir)})",
        "Dataset fingerprint": dataset_fingerprint(clean_df)[:16],
        "Embedding model": settings.embedding_model,
        "Chroma collection": f"{index.collection_name} ({index.collection.count()} docs)",
        "Test set": f"{len(test_set)} questions ({paths.eval_testset.relative_to(paths.project_dir)})",
        "Top-k": settings.top_k,
    }
    generate_phase1_report(paths.baseline_report, source_summary, metrics, quality, freshness)
    print(f"[phase1] Report -> {paths.baseline_report}")

    _run_agent_demo(settings, index, test_set)
    print("[phase1] Done.")
