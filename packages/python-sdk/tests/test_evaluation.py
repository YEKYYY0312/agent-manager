"""Tests for dataset-based deterministic and human evaluation."""

from __future__ import annotations

import json

import pytest

from agent_devtools.evaluation import Annotation, AnnotationStore, evaluate_dataset, load_dataset


def _dataset(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({
        "version": 1,
        "name": "support",
        "cases": [
            {"id": "complete", "difficulty": "easy", "expected_key_points": ["billing", "limit"], "bad_answer_patterns": ["guaranteed"]},
            {"id": "unsafe", "difficulty": "hard", "expected_key_points": ["refund"], "bad_answer_patterns": ["guaranteed"]},
        ],
    }), encoding="utf-8")
    return path


def test_evaluate_dataset_reports_rubric_scores_strata_and_failure_clusters(tmp_path) -> None:
    dataset = load_dataset(_dataset(tmp_path))

    report = evaluate_dataset(dataset, {"complete": "Billing limit details.", "unsafe": "A guaranteed response."})

    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.by_difficulty["easy"]["pass_rate"] == 1.0
    assert report.by_difficulty["hard"]["pass_rate"] == 0.0
    assert report.results[0].overall_score == 1.0
    assert report.results[1].forbidden_patterns == ["guaranteed"]
    assert report.failure_clusters[0].signature == "forbidden_pattern:guaranteed"
    assert report.failure_clusters[0].case_ids == ["unsafe"]


def test_human_annotations_are_persisted_and_included_in_quality_score(tmp_path) -> None:
    dataset = load_dataset(_dataset(tmp_path))
    store = AnnotationStore(tmp_path / "annotations.jsonl")
    store.append(Annotation(case_id="complete", reviewer="alice", scores={"accuracy": 0.8, "completeness": 0.6}))

    report = evaluate_dataset(dataset, {"complete": "billing limit", "unsafe": "refund"}, annotations=store.load())

    assert report.results[0].human_score == pytest.approx(0.7)
    assert report.results[0].overall_score == pytest.approx(0.85)
    assert store.load()[0].reviewer == "alice"


def test_load_dataset_rejects_duplicate_case_ids(tmp_path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps({"version": 1, "name": "test", "cases": [{"id": "same"}, {"id": "same"}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_dataset(path)
