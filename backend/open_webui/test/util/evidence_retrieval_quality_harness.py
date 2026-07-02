from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(slots=True)
class RetrievalQualityCase:
    case_id: str
    expected_ref: str
    expected_modality: str | None
    hits: list[dict[str, Any]]


def _normalize_case(payload: dict[str, Any]) -> RetrievalQualityCase:
    return RetrievalQualityCase(
        case_id=str(payload.get("case_id") or payload.get("id") or "case"),
        expected_ref=str(payload["expected_ref"]),
        expected_modality=(
            str(payload["expected_modality"]).strip().lower()
            if payload.get("expected_modality") is not None
            else None
        ),
        hits=[dict(hit) for hit in payload.get("hits") or []],
    )


def load_cases(path: str | Path) -> list[RetrievalQualityCase]:
    payload = json.loads(Path(path).read_text())
    rows = payload if isinstance(payload, list) else payload.get("cases") or []
    return [_normalize_case(dict(row)) for row in rows]


def compute_quality_metrics(cases: Sequence[RetrievalQualityCase], *, top_k: int = 3) -> dict[str, Any]:
    total = len(cases)
    top1 = 0
    topk = 0
    image_hit = 0
    detailed_rows: list[dict[str, Any]] = []

    for case in cases:
        ranked_hits = list(case.hits)
        top_hits = ranked_hits[: max(1, int(top_k))]
        top_refs = [str(hit.get("evidence_ref") or "") for hit in top_hits]
        first_ref = top_refs[0] if top_refs else None
        first_hit = ranked_hits[0] if ranked_hits else None

        top1_match = first_ref == case.expected_ref
        topk_match = case.expected_ref in top_refs
        image_match = any(str(hit.get("modality") or "").strip().lower() == "image" for hit in top_hits)

        top1 += int(top1_match)
        topk += int(topk_match)
        image_hit += int(image_match)
        detailed_rows.append(
            {
                "case_id": case.case_id,
                "expected_ref": case.expected_ref,
                "expected_modality": case.expected_modality,
                "top1_ref": first_ref,
                "top1_modality": str(first_hit.get("modality") or "").strip().lower() if first_hit else None,
                "top1_match": top1_match,
                "topk_match": topk_match,
                "image_hit": image_match,
                "returned_refs": top_refs,
            }
        )

    return {
        "total_cases": total,
        "top1_accuracy": (top1 / total) if total else 0.0,
        "topk_recall": (topk / total) if total else 0.0,
        "image_hit_rate": (image_hit / total) if total else 0.0,
        "details": detailed_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score offline multimodal evidence retrieval runs from local JSON.")
    parser.add_argument("input", help="Path to a local JSON file containing retrieval cases")
    parser.add_argument("--top-k", type=int, default=3, help="Top-k cutoff for recall/image-hit scoring")
    args = parser.parse_args()

    metrics = compute_quality_metrics(load_cases(args.input), top_k=args.top_k)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
