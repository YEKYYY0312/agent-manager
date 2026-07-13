"""Offline dataset evaluation with deterministic rubrics and human annotations.

The deterministic scorer intentionally checks explicit requirements only. Human
annotations can supply semantic judgement without sending Trace data to a model.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    difficulty: str = "medium"
    expected_key_points: tuple[str, ...] = ()
    bad_answer_patterns: tuple[str, ...] = ()
    category: str = ""
    question: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationDataset:
    name: str
    version: int
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True)
class Annotation:
    case_id: str
    reviewer: str
    scores: dict[str, float]
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def __post_init__(self) -> None:
        if not self.case_id or not self.reviewer:
            raise ValueError("case_id and reviewer are required")
        if not self.scores:
            raise ValueError("at least one annotation score is required")
        for dimension, score in self.scores.items():
            if not dimension or isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
                raise ValueError("annotation scores must be between 0 and 1")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Annotation":
        return cls(
            case_id=str(value["case_id"]),
            reviewer=str(value["reviewer"]),
            scores={str(name): float(score) for name, score in dict(value["scores"]).items()},
            note=str(value.get("note", "")),
            created_at=str(value.get("created_at", "")) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )


class AnnotationStore:
    """Append-only local annotation records suitable for code review."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, annotation: Annotation) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(annotation), ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n")

    def load(self) -> list[Annotation]:
        if not self.path.exists():
            return []
        annotations: list[Annotation] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("annotation must be an object")
                annotations.append(Annotation.from_dict(value))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid annotation at line {line_number}") from exc
        return annotations


@dataclass(frozen=True)
class EvaluationResult:
    case_id: str
    difficulty: str
    matched_key_points: list[str]
    missing_key_points: list[str]
    forbidden_patterns: list[str]
    deterministic_score: float
    human_score: float | None
    overall_score: float
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FailureCluster:
    signature: str
    case_ids: list[str]

    @property
    def count(self) -> int:
        return len(self.case_ids)

    def to_dict(self) -> dict[str, object]:
        return {"signature": self.signature, "count": self.count, "case_ids": self.case_ids}


@dataclass(frozen=True)
class EvaluationReport:
    dataset_name: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    results: list[EvaluationResult]
    by_difficulty: dict[str, dict[str, float | int]]
    failure_clusters: list[FailureCluster]

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset_name": self.dataset_name,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "pass_rate": self.pass_rate,
            "results": [result.to_dict() for result in self.results],
            "by_difficulty": self.by_difficulty,
            "failure_clusters": [cluster.to_dict() for cluster in self.failure_clusters],
        }


def load_dataset(path: str | Path) -> EvaluationDataset:
    """Load and validate a versioned evaluation dataset JSON file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read evaluation dataset: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), int) or not isinstance(raw.get("name"), str):
        raise ValueError("dataset must contain integer version and string name")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("dataset must contain at least one case")
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or not isinstance(raw_case.get("id"), str) or not raw_case["id"]:
            raise ValueError("each dataset case requires an id")
        case_id = raw_case["id"]
        if case_id in seen_ids:
            raise ValueError(f"duplicate dataset case id: {case_id}")
        seen_ids.add(case_id)
        cases.append(EvaluationCase(
            id=case_id,
            difficulty=_string(raw_case.get("difficulty"), "medium"),
            expected_key_points=_strings(raw_case.get("expected_key_points"), "expected_key_points"),
            bad_answer_patterns=_strings(raw_case.get("bad_answer_patterns"), "bad_answer_patterns"),
            category=_string(raw_case.get("category")),
            question=_string(raw_case.get("question")),
            tags=_strings(raw_case.get("tags"), "tags"),
        ))
    return EvaluationDataset(name=raw["name"], version=raw["version"], cases=tuple(cases))


def load_answers(path: str | Path) -> dict[str, str]:
    """Load answers from either ``{case_id: answer}`` or an ``answers`` list."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read evaluation answers: {path}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("answers"), list):
        pairs = raw["answers"]
        answers = {str(item["id"]): str(item["answer"]) for item in pairs if isinstance(item, dict) and "id" in item and "answer" in item}
        if len(answers) != len(pairs):
            raise ValueError("each answer must contain id and answer")
        return answers
    if isinstance(raw, dict) and all(isinstance(case_id, str) and isinstance(answer, str) for case_id, answer in raw.items()):
        return dict(raw)
    raise ValueError("answers must be an object or an answers list")


def evaluate_dataset(
    dataset: EvaluationDataset,
    answers: Mapping[str, str],
    *,
    annotations: Iterable[Annotation] = (),
    threshold: float = 0.7,
) -> EvaluationReport:
    """Score a completed dataset without calling a model or external service."""
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    annotation_scores = _annotation_scores(annotations)
    results = [_evaluate_case(case, answers.get(case.id, ""), annotation_scores.get(case.id, ()), float(threshold)) for case in dataset.cases]
    passed_cases = sum(result.passed for result in results)
    return EvaluationReport(
        dataset_name=dataset.name,
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=passed_cases / len(results),
        results=results,
        by_difficulty=_stratify(results),
        failure_clusters=_cluster_failures(results),
    )


def _evaluate_case(case: EvaluationCase, answer: str, annotations: tuple[Annotation, ...], threshold: float) -> EvaluationResult:
    normalized = answer.casefold()
    matched = [point for point in case.expected_key_points if point.casefold() in normalized]
    missing = [point for point in case.expected_key_points if point not in matched]
    forbidden = [pattern for pattern in case.bad_answer_patterns if pattern.casefold() in normalized]
    coverage = len(matched) / len(case.expected_key_points) if case.expected_key_points else 1.0
    safety = 0.0 if forbidden else 1.0
    deterministic = (coverage + safety) / 2
    human_score = _mean(score for annotation in annotations for score in annotation.scores.values())
    overall = deterministic if human_score is None else (deterministic + human_score) / 2
    return EvaluationResult(
        case_id=case.id,
        difficulty=case.difficulty,
        matched_key_points=matched,
        missing_key_points=missing,
        forbidden_patterns=forbidden,
        deterministic_score=deterministic,
        human_score=human_score,
        overall_score=overall,
        passed=not forbidden and overall >= threshold,
    )


def _annotation_scores(annotations: Iterable[Annotation]) -> dict[str, tuple[Annotation, ...]]:
    grouped: dict[str, list[Annotation]] = defaultdict(list)
    for annotation in annotations:
        grouped[annotation.case_id].append(annotation)
    return {case_id: tuple(values) for case_id, values in grouped.items()}


def _stratify(results: Iterable[EvaluationResult]) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[EvaluationResult]] = defaultdict(list)
    for result in results:
        groups[result.difficulty].append(result)
    return {
        difficulty: {
            "total_cases": len(values),
            "passed_cases": sum(value.passed for value in values),
            "pass_rate": sum(value.passed for value in values) / len(values),
            "average_score": sum(value.overall_score for value in values) / len(values),
        }
        for difficulty, values in sorted(groups.items())
    }


def _cluster_failures(results: Iterable[EvaluationResult]) -> list[FailureCluster]:
    groups: dict[str, list[str]] = defaultdict(list)
    for result in results:
        if result.passed:
            continue
        if result.forbidden_patterns:
            signature = f"forbidden_pattern:{result.forbidden_patterns[0]}"
        elif result.missing_key_points:
            signature = f"missing_expected_key_point:{result.missing_key_points[0]}"
        else:
            signature = "below_threshold"
        groups[signature].append(result.case_id)
    return [FailureCluster(signature, case_ids) for signature, case_ids in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))]


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{field_name} must be an array of non-empty strings")
    return tuple(value)


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("dataset text fields must be strings")
    return value
