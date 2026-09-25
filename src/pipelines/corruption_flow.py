from __future__ import annotations

from typing import Any

import pandas as pd

from core.config import Settings, load_settings
from core.utils import now_utc, read_json, write_json
from evaluation.metrics import evaluate_pipeline
from ingestion.cleaning import build_clean_dataframe
from ingestion.corruption import corrupt_clean_dataframe
from ingestion.crossref import load_raw_records
from observability.quality import build_freshness_report, run_data_quality_checks
from observability.reporting import HEADLINE_METRICS, generate_corruption_report
from pipelines.phase1 import QualityGateError, dataset_fingerprint, enrich_metrics_with_breakdown, load_dataframe, save_dataframe
from retrieval.index import LocalEmbeddingIndex


def repair_from_raw(settings: Settings) -> pd.DataFrame:
    """Idempotent repair: rebuild the clean dataset from the raw snapshot, never patch in place."""
    records = load_raw_records(settings.paths.raw_records_json)
    return build_clean_dataframe(records, now_utc())


def _print_comparison(baseline: dict[str, Any], corrupted: dict[str, Any], repaired: dict[str, Any]) -> None:
    header = f"{'Metric':<24}{'Baseline':>12}{'Corrupted':>12}{'Repaired':>12}"
    print("\n" + header)
    print("-" * len(header))
    for key, label in HEADLINE_METRICS:
        print(f"{label:<24}{baseline[key]:>12.4f}{corrupted[key]:>12.4f}{repaired[key]:>12.4f}")
    print()


def main() -> None:
    settings = load_settings()
    paths = settings.paths
    if not paths.baseline_metrics.exists() or not paths.clean_json.exists() or not paths.eval_testset.exists():
        raise FileNotFoundError("Baseline artifacts missing — run `python script/run_phase1.py` first.")

    baseline_metrics = read_json(paths.baseline_metrics)
    baseline_quality = read_json(paths.baseline_quality_report) if paths.baseline_quality_report.exists() else None
    clean_df = load_dataframe(paths.clean_json)
    print(f"[corruption] Loaded baseline: {len(clean_df)} clean rows, hit_rate={baseline_metrics['retrieval_hit_rate']:.4f}")

    corrupted_df = corrupt_clean_dataframe(clean_df, paths.corruption_log)
    save_dataframe(corrupted_df, paths.corrupted_clean_csv, paths.corrupted_clean_json)
    corruption_log = read_json(paths.corruption_log)
    for item in corruption_log["corruptions"]:
        print(f"[corruption]   - {item['corruption_type']:<20} rows={item['affected_rows']}")
    print(f"[corruption] Corrupted rows: {len(corrupted_df)} -> {paths.corrupted_clean_csv}")

    corrupted_quality = run_data_quality_checks(corrupted_df, settings, "corrupted")
    corrupted_freshness_path = settings.paths.quality_dir / "corrupted_freshness_report.json"
    corrupted_freshness = build_freshness_report(corrupted_df, settings, corrupted_freshness_path)
    gate_tripped = not corrupted_quality["success"] or not corrupted_freshness["is_fresh"]
    print(
        f"[corruption] ALERT GX success={corrupted_quality['success']} failed={corrupted_quality['failed_expectations']} | "
        f"is_fresh={corrupted_freshness['is_fresh']} (stale ratio {corrupted_freshness['stale_ratio']:.2%})"
    )

    corrupted_index = LocalEmbeddingIndex.build(corrupted_df, settings, paths.corrupted_embeddings_json)
    evaluate_pipeline(settings, corrupted_index, paths.eval_testset, paths.corrupted_metrics, paths.corrupted_answers)
    corrupted_metrics = enrich_metrics_with_breakdown(paths.corrupted_metrics, paths.corrupted_answers)
    print(
        f"[corruption] Corrupted '{corrupted_index.collection_name}': hit_rate={corrupted_metrics['retrieval_hit_rate']:.4f} "
        f"token_f1={corrupted_metrics['mean_token_f1']:.4f}"
    )

    if gate_tripped:
        print("[repair] Quality gate tripped -> rebuilding dataset from raw snapshot")
    else:
        print("[repair] Gate did not trip, running repair anyway for the comparison")
    repaired_df = repair_from_raw(settings)
    second_rebuild = repair_from_raw(settings)
    dataset_check = {
        "baseline_fingerprint": dataset_fingerprint(clean_df),
        "repaired_fingerprint": dataset_fingerprint(repaired_df),
        "second_rebuild_fingerprint": dataset_fingerprint(second_rebuild),
    }
    dataset_check["matches_baseline"] = dataset_check["repaired_fingerprint"] == dataset_check["baseline_fingerprint"]
    dataset_check["rebuild_is_deterministic"] = dataset_check["repaired_fingerprint"] == dataset_check["second_rebuild_fingerprint"]
    save_dataframe(repaired_df, paths.repaired_clean_csv, paths.repaired_clean_json)

    repaired_quality = run_data_quality_checks(repaired_df, settings, "repaired")
    repaired_freshness_path = settings.paths.quality_dir / "repaired_freshness_report.json"
    repaired_freshness = build_freshness_report(repaired_df, settings, repaired_freshness_path)
    print(
        f"[repair] Repaired rows: {len(repaired_df)} | GX success={repaired_quality['success']} | is_fresh={repaired_freshness['is_fresh']} | "
        f"matches_baseline={dataset_check['matches_baseline']} deterministic={dataset_check['rebuild_is_deterministic']}"
    )
    if not repaired_quality["success"]:
        raise QualityGateError(f"Repaired data still fails the gate: {repaired_quality['failed_expectations']}")

    repaired_index = LocalEmbeddingIndex.build(repaired_df, settings, paths.repaired_embeddings_json)
    evaluate_pipeline(settings, repaired_index, paths.eval_testset, paths.repaired_metrics, paths.repaired_answers)
    repaired_metrics = enrich_metrics_with_breakdown(paths.repaired_metrics, paths.repaired_answers)
    print(
        f"[repair] Repaired '{repaired_index.collection_name}': hit_rate={repaired_metrics['retrieval_hit_rate']:.4f} "
        f"token_f1={repaired_metrics['mean_token_f1']:.4f}"
    )

    write_json(paths.corrupted_metrics.parent / "dataset_check.json", dataset_check)
    generate_corruption_report(
        paths.comparison_report,
        baseline_metrics,
        corrupted_metrics,
        repaired_metrics,
        corrupted_quality,
        repaired_quality,
        corrupted_freshness,
        repaired_freshness,
        corruption_log=corruption_log,
        dataset_check=dataset_check,
        baseline_quality=baseline_quality,
        corrupted_answers=read_json(paths.corrupted_answers),
    )
    _print_comparison(baseline_metrics, corrupted_metrics, repaired_metrics)
    print(f"[corruption] Report -> {paths.comparison_report}")
