from __future__ import annotations

from typing import Any

from core.utils import now_utc, write_text

HEADLINE_METRICS = [
    ("retrieval_hit_rate", "Retrieval Hit Rate"),
    ("mean_token_f1", "Mean Token F1"),
    ("judge_accuracy", "Judge Accuracy"),
    ("mean_judge_score", "Mean Judge Score (1-5)"),
]


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return "-"
    return str(value)


def _delta(value: Any, reference: Any) -> str:
    if not isinstance(value, (int, float)) or not isinstance(reference, (int, float)) or isinstance(value, bool):
        return "-"
    return f"{value - reference:+.4f}"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(_fmt(cell) for cell in row) + " |" for row in rows]
    return lines


def _quality_lines(quality: dict[str, Any]) -> list[str]:
    rows = [
        [
            item["expectation"],
            item.get("column") or "(table)",
            item["success"],
            item.get("observed_value") if item.get("observed_value") is not None else item.get("unexpected_count"),
        ]
        for item in quality.get("expectations", [])
    ]
    return [
        f"- Engine: `{quality.get('engine', 'great_expectations')}` (ephemeral context, pandas source + whole-dataframe batch)",
        f"- Suite result: **{_fmt(quality.get('success'))}** "
        f"({quality.get('successful_expectations')}/{quality.get('evaluated_expectations')} expectations passed)",
        "",
        *_table(["Expectation", "Column", "Success", "Observed / unexpected"], rows),
    ]


def _freshness_lines(freshness: dict[str, Any]) -> list[str]:
    return _table(
        ["Field", "Value"],
        [
            ["Threshold (days)", freshness.get("threshold_days")],
            ["Latest published", freshness.get("latest_published")],
            ["Oldest published", freshness.get("oldest_published")],
            ["Stale rows / total", f"{freshness.get('stale_rows')} / {freshness.get('total_rows')}"],
            ["Stale ratio (max allowed)", f"{freshness.get('stale_ratio', 0):.2%} ({freshness.get('max_stale_ratio', 0.25):.0%})"],
            ["is_fresh", freshness.get("is_fresh")],
        ],
    )


def _by_type_rows(states: list[tuple[str, dict[str, Any]]], metric: str) -> list[list[Any]]:
    types = sorted({k for _, m in states for k in (m.get("by_question_type") or {})})
    return [
        [t] + [(m.get("by_question_type") or {}).get(t, {}).get(metric) for _, m in states]
        for t in types
    ]


def generate_phase1_report(
    report_path,
    source_summary: dict[str, Any],
    metrics: dict[str, Any],
    quality: dict[str, Any],
    freshness: dict[str, Any],
) -> None:
    """Write the baseline (phase 1) markdown report."""
    by_type = metrics.get("by_question_type") or {}
    lines = [
        "# Phase 1 Report — Baseline Pipeline",
        "",
        f"_Generated at {now_utc().isoformat()}_",
        "",
        "## 1. Source and lineage",
        "",
        *_table(["Field", "Value"], [[k, v] for k, v in source_summary.items()]),
        "",
        "## 2. Retrieval and answer quality (baseline)",
        "",
        *_table(
            ["Metric", "Value"],
            [[label, metrics.get(key)] for key, label in HEADLINE_METRICS]
            + [["Samples", metrics.get("samples")], ["Judge mode", metrics.get("judge_mode")]],
        ),
        "",
        "### Per question type",
        "",
        *_table(
            ["Question type", "Hit rate", "Token F1", "Judge accuracy"],
            [
                [t, v.get("retrieval_hit_rate"), v.get("mean_token_f1"), v.get("judge_accuracy")]
                for t, v in by_type.items()
            ],
        ),
        "",
        "## 3. Data quality gate (Great Expectations 1.x)",
        "",
        *_quality_lines(quality),
        "",
        "## 4. Freshness SLA",
        "",
        *_freshness_lines(freshness),
        "",
        "## 5. Verdict",
        "",
        f"- Quality gate: **{'PASSED' if quality.get('success') else 'FAILED'}**.",
        f"- Freshness: **{'FRESH' if freshness.get('is_fresh') else 'STALE'}** "
        f"({freshness.get('stale_rows')}/{freshness.get('total_rows')} rows older than {freshness.get('threshold_days')} days).",
        f"- Baseline hit rate {_fmt(metrics.get('retrieval_hit_rate'))}, token F1 {_fmt(metrics.get('mean_token_f1'))}: "
        "reference numbers for the corruption experiment.",
        "",
    ]
    write_text(report_path, "\n".join(lines))


def _analysis_lines(
    baseline: dict[str, Any],
    corrupted: dict[str, Any],
    repaired: dict[str, Any],
    corrupted_quality: dict[str, Any],
    corrupted_freshness: dict[str, Any],
    repaired_quality: dict[str, Any],
    repaired_freshness: dict[str, Any],
    dataset_check: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    for key, label in HEADLINE_METRICS:
        base, bad, fixed = baseline.get(key), corrupted.get(key), repaired.get(key)
        if not all(isinstance(v, (int, float)) for v in (base, bad, fixed)):
            continue
        change = (bad - base) / base if base else 0.0
        status = (
            "fully recovered"
            if abs(fixed - base) < 1e-9
            else "partially recovered"
            if fixed > bad
            else "not recovered"
        )
        lines.append(
            f"- **{label}**: {base:.4f} -> {bad:.4f} ({change:+.1%} relative) -> {fixed:.4f} after repair ({status})."
        )
    base_by_type = baseline.get("by_question_type") or {}
    bad_by_type = corrupted.get("by_question_type") or {}
    drops = sorted(
        (
            (base_by_type[t]["mean_token_f1"] - bad_by_type[t]["mean_token_f1"], t)
            for t in base_by_type
            if t in bad_by_type
        ),
        reverse=True,
    )
    if drops and drops[0][0] > 0:
        lines.append(f"- Largest token-F1 drop by question type: `{drops[0][1]}` (-{drops[0][0]:.4f}).")
    failed = corrupted_quality.get("failed_expectations") or []
    lines.append(
        "- GX gate flagged the corrupted batch with "
        f"{len(failed)} failed expectation(s): "
        + (", ".join(f"`{n}`" for n in failed) if failed else "none")
        + "."
    )
    lines.append(
        f"- Freshness on corrupted data: stale ratio {corrupted_freshness.get('stale_ratio', 0):.2%} -> "
        f"`is_fresh={corrupted_freshness.get('is_fresh')}`."
    )
    lines.append(
        f"- Repaired data re-passed the gate (`success={repaired_quality.get('success')}`, "
        f"`is_fresh={repaired_freshness.get('is_fresh')}`)."
    )
    if dataset_check:
        lines.append(
            f"- Idempotency: repaired fingerprint `{dataset_check.get('repaired_fingerprint', '')[:12]}` "
            f"{'matches' if dataset_check.get('matches_baseline') else 'does NOT match'} baseline "
            f"`{dataset_check.get('baseline_fingerprint', '')[:12]}`; second rebuild gave "
            f"{'the same' if dataset_check.get('rebuild_is_deterministic') else 'a DIFFERENT'} fingerprint."
        )
    return lines


def _silent_failure_lines(corrupted_answers: list[dict[str, Any]] | None) -> list[str]:
    if not corrupted_answers:
        return []
    wrong = [a for a in corrupted_answers if a["token_f1"] < 1.0 or not a["retrieval_hit"]]
    answered = sum(1 for a in corrupted_answers if str(a["answer"]).strip())
    rows = [
        [a["id"], a["question_type"], a["retrieval_hit"], a["ground_truth"][:70], str(a["answer"])[:70] or "(empty)", round(float(a["token_f1"]), 4)]
        for a in wrong
    ]
    return [
        "### Why this is a silent failure",
        "",
        f"On the corrupted index the QA path answered {answered}/{len(corrupted_answers)} questions with no error, "
        f"yet {len(wrong)} answers were wrong or degraded. Only the GX gate and freshness SLA signalled a problem.",
        "",
        *_table(["ID", "Type", "Hit", "Ground truth", "Corrupted answer", "Token F1"], rows),
        "",
    ]


def generate_corruption_report(
    report_path,
    baseline_metrics: dict[str, Any],
    corrupted_metrics: dict[str, Any],
    repaired_metrics: dict[str, Any],
    corrupted_quality: dict[str, Any],
    repaired_quality: dict[str, Any],
    corrupted_freshness: dict[str, Any],
    repaired_freshness: dict[str, Any],
    corruption_log: dict[str, Any] | None = None,
    dataset_check: dict[str, Any] | None = None,
    baseline_quality: dict[str, Any] | None = None,
    corrupted_answers: list[dict[str, Any]] | None = None,
) -> None:
    """Write the Baseline vs Corrupted vs Repaired comparison report."""
    states = [("Baseline", baseline_metrics), ("Corrupted", corrupted_metrics), ("Repaired", repaired_metrics)]
    metric_rows = [
        [label, baseline_metrics.get(key), corrupted_metrics.get(key), repaired_metrics.get(key),
         _delta(corrupted_metrics.get(key), baseline_metrics.get(key)), _delta(repaired_metrics.get(key), baseline_metrics.get(key))]
        for key, label in HEADLINE_METRICS
    ]
    metric_rows.append(["Samples", baseline_metrics.get("samples"), corrupted_metrics.get("samples"), repaired_metrics.get("samples"), "-", "-"])
    baseline_quality = baseline_quality or {}
    signal_rows = [
        ["Rows validated", baseline_quality.get("row_count"), corrupted_quality.get("row_count"), repaired_quality.get("row_count")],
        ["GX suite success", baseline_quality.get("success"), corrupted_quality.get("success"), repaired_quality.get("success")],
        ["Expectations passed",
         f"{baseline_quality.get('successful_expectations', '-')}/{baseline_quality.get('evaluated_expectations', '-')}",
         f"{corrupted_quality.get('successful_expectations')}/{corrupted_quality.get('evaluated_expectations')}",
         f"{repaired_quality.get('successful_expectations')}/{repaired_quality.get('evaluated_expectations')}"],
        ["Latest published", (baseline_quality.get("freshness") or {}).get("latest_published"), corrupted_freshness.get("latest_published"), repaired_freshness.get("latest_published")],
        ["Stale ratio", (baseline_quality.get("freshness") or {}).get("stale_ratio"), corrupted_freshness.get("stale_ratio"), repaired_freshness.get("stale_ratio")],
        ["is_fresh", (baseline_quality.get("freshness") or {}).get("is_fresh"), corrupted_freshness.get("is_fresh"), repaired_freshness.get("is_fresh")],
    ]
    corrupted_by_name = {(i["expectation"], i.get("column")): i for i in corrupted_quality.get("expectations", [])}
    expectation_rows = [
        [i["expectation"], i.get("column") or "(table)",
         corrupted_by_name.get((i["expectation"], i.get("column")), {}).get("success"),
         corrupted_by_name.get((i["expectation"], i.get("column")), {}).get("unexpected_count"),
         i["success"]]
        for i in repaired_quality.get("expectations", [])
    ]
    lines = [
        "# Corruption Report — Baseline vs Corrupted vs Repaired",
        "",
        f"_Generated at {now_utc().isoformat()}_ — all three states evaluated on the same `data/eval/test_set.json`.",
        "",
        "## 1. Headline metrics (3 states)",
        "",
        *_table(["Metric", "Baseline", "Corrupted", "Repaired", "Δ Corrupted", "Δ Repaired"], metric_rows),
        "",
        "### Token F1 by question type",
        "",
        *_table(["Question type", "Baseline", "Corrupted", "Repaired"], _by_type_rows(states, "mean_token_f1")),
        "",
        "### Retrieval hit rate by question type",
        "",
        *_table(["Question type", "Baseline", "Corrupted", "Repaired"], _by_type_rows(states, "retrieval_hit_rate")),
        "",
        "## 2. Observability signals",
        "",
        *_table(["Signal", "Baseline", "Corrupted", "Repaired"], signal_rows),
        "",
        "### Expectation-level detail",
        "",
        *_table(["Expectation", "Column", "Corrupted success", "Corrupted unexpected", "Repaired success"], expectation_rows),
        "",
    ]
    if corruption_log:
        lines += [
            "## 3. Injected corruptions",
            "",
            f"Seed `{corruption_log.get('seed')}` — {corruption_log.get('input_rows')} clean rows -> {corruption_log.get('output_rows')} corrupted rows.",
            "",
            *_table(["#", "Corruption", "Rows", "Description"],
                    [[n, item["corruption_type"], item["affected_rows"], item["description"]] for n, item in enumerate(corruption_log.get("corruptions", []), start=1)]),
            "",
        ]
    lines += [
        "## 4. Analysis",
        "",
        *_analysis_lines(baseline_metrics, corrupted_metrics, repaired_metrics, corrupted_quality,
                         corrupted_freshness, repaired_quality, repaired_freshness, dataset_check),
        "",
        *_silent_failure_lines(corrupted_answers),
        "### How the repair works",
        "",
        "Repair never patches the corrupted table. It rebuilds the clean dataset from the immutable raw snapshot "
        "with the same cleaning code, re-validates it with the GX gate and re-indexes it into a separate "
        "`papers-repaired` collection. Deterministic input plus deterministic transformation means any number of "
        "repair runs yield the same dataset.",
        "",
    ]
    write_text(report_path, "\n".join(lines))
