"""
Offline evaluation helpers for recommendation quality.

This gives us a reproducible way to track whether recommender changes improve
top-1 quality instead of relying on ad-hoc manual checks.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


@dataclass
class RecommendationEvalCase:
    query: str
    creator_id: int
    ideal_titles: List[str]
    acceptable_titles: List[str]
    bad_titles: List[str]
    metadata: Optional[Dict[str, Any]] = None


def load_eval_cases(path: Path) -> List[RecommendationEvalCase]:
    cases: List[RecommendationEvalCase] = []
    if not path.exists():
        return cases
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean:
            continue
        data = json.loads(clean)
        cases.append(
            RecommendationEvalCase(
                query=str(data.get("query") or ""),
                creator_id=int(data.get("creator_id") or 0),
                ideal_titles=[str(item) for item in data.get("ideal_titles") or []],
                acceptable_titles=[str(item) for item in data.get("acceptable_titles") or []],
                bad_titles=[str(item) for item in data.get("bad_titles") or []],
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            )
        )
    return cases


def _normalize_title(value: str) -> str:
    return " ".join(str(value or "").lower().split()).strip()


def _graded_relevance(title: str, case: RecommendationEvalCase) -> int:
    normalized = _normalize_title(title)
    if normalized in {_normalize_title(item) for item in case.ideal_titles}:
        return 3
    if normalized in {_normalize_title(item) for item in case.acceptable_titles}:
        return 2
    if normalized in {_normalize_title(item) for item in case.bad_titles}:
        return 0
    return 1 if normalized else 0


def ndcg_at_k(ranked_titles: List[str], case: RecommendationEvalCase, k: int = 5) -> float:
    ranked = ranked_titles[:k]
    if not ranked:
        return 0.0

    dcg = 0.0
    for idx, title in enumerate(ranked, start=1):
        rel = _graded_relevance(title, case)
        dcg += (2 ** rel - 1) / math.log2(idx + 1)

    ideal_pool = list(case.ideal_titles) + list(case.acceptable_titles)
    ideal_rels = sorted((_graded_relevance(title, case) for title in ideal_pool), reverse=True)[:k]
    if not ideal_rels:
        return 0.0

    idcg = 0.0
    for idx, rel in enumerate(ideal_rels, start=1):
        idcg += (2 ** rel - 1) / math.log2(idx + 1)
    return dcg / idcg if idcg else 0.0


def evaluate_cases(
    predictor: Callable[[RecommendationEvalCase], List[str]],
    cases: Iterable[RecommendationEvalCase],
) -> Dict[str, Any]:
    case_list = list(cases)
    if not case_list:
        return {"case_count": 0, "top1_accuracy": 0.0, "ndcg@5": 0.0, "cases": []}

    results = []
    top1_hits = 0
    ndcg_scores = []
    for case in case_list:
        ranked_titles = predictor(case)
        top1 = ranked_titles[0] if ranked_titles else ""
        top1_hit = _graded_relevance(top1, case) >= 2
        if top1_hit:
            top1_hits += 1
        ndcg_score = ndcg_at_k(ranked_titles, case, k=5)
        ndcg_scores.append(ndcg_score)
        results.append(
            {
                "query": case.query,
                "creator_id": case.creator_id,
                "top1": top1,
                "top1_hit": top1_hit,
                "ndcg@5": ndcg_score,
            }
        )
    return {
        "case_count": len(case_list),
        "top1_accuracy": top1_hits / len(case_list),
        "ndcg@5": sum(ndcg_scores) / len(ndcg_scores),
        "cases": results,
    }

