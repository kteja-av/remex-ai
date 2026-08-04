import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.policy import payload_from_request
from app.db.session import get_connection
from app.domain.memory import MemoryType
from app.embedding.local_encoder import EMBEDDING_DIMENSION, get_encoder
from app.retrieval.vector import retrieve_similar, store_memory
from worker.write_gate import evaluate_candidate


def _load_suite(name: str) -> dict[str, Any]:
    path = Path("evals") / "suites" / f"{name}.json"
    if not path.exists():
        raise ValueError(f"unknown eval suite: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def compute_metrics(
    *,
    admitted: set[str],
    should_admit: set[str],
    query_results: list[dict[str, Any]],
    k: int,
) -> dict[str, float | int]:
    correctly_admitted = len(admitted.intersection(should_admit))
    total_retrieved = sum(len(result["retrieved"]) for result in query_results)
    total_matched = sum(int(result["matched"]) for result in query_results)
    return {
        # M3 admits every candidate. M5 must improve admission precision while
        # preserving recall on the exact same labeled candidate set.
        "precision": correctly_admitted / len(admitted) if admitted else 0.0,
        "recall": (
            correctly_admitted / len(should_admit) if should_admit else 0.0
        ),
        # Micro precision@k penalizes false-positive retrievals, including
        # no-answer queries. A later retriever can improve by abstaining.
        "precision_at_k": (
            total_matched / total_retrieved if total_retrieved else 0.0
        ),
        "k": k,
    }


def compare_reports(
    report: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, float]:
    current_id = report["dataset"]["id"]
    baseline_id = baseline["dataset"]["id"]
    if current_id != baseline_id:
        raise ValueError(
            "cannot compare reports from different labeled datasets: "
            f"{current_id!r} != {baseline_id!r}"
        )
    return {
        metric: report["metrics"][metric] - baseline["metrics"][metric]
        for metric in ("precision", "recall", "precision_at_k")
    }


def evaluate_suite(name: str) -> dict[str, Any]:
    suite = _load_suite(name)
    encoder = get_encoder()
    tenant_id, user_id = uuid4(), uuid4()
    labels_by_id: dict[str, str] = {}

    try:
        for candidate in suite["memories"]:
            if name == "write_gate":
                payload = payload_from_request(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_type=MemoryType.SEMANTIC,
                    content=candidate["content"],
                    source_turn_ids=[uuid4()],
                    importance=0.5,
                )
                result = evaluate_candidate(payload)
                if result["outcome"] != "admitted":
                    continue
                labels_by_id[result["memory_id"]] = candidate["label"]
                continue

            record = store_memory(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_type=MemoryType.SEMANTIC,
                content=candidate["content"],
                source_turn_ids=[uuid4()],
                embedding=encoder.encode(candidate["content"]),
            )
            labels_by_id[str(record.id)] = candidate["label"]

        k = int(suite["k"])
        query_results: list[dict[str, Any]] = []

        for query in suite["queries"]:
            hits = retrieve_similar(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=encoder.encode(query["text"]),
                limit=k,
            )
            retrieved = [labels_by_id[str(hit.memory.id)] for hit in hits]
            relevant = set(query["relevant"])
            matched = len(relevant.intersection(retrieved))
            query_results.append(
                {
                    "query": query["text"],
                    "relevant": sorted(relevant),
                    "retrieved": retrieved,
                    "matched": matched,
                }
            )

        query_count = len(suite["queries"])
        admitted = set(labels_by_id.values())
        should_admit = {
            candidate["label"]
            for candidate in suite["memories"]
            if candidate["should_admit"]
        }
        metrics = compute_metrics(
            admitted=admitted,
            should_admit=should_admit,
            query_results=query_results,
            k=k,
        )
        return {
            "schema_version": 2,
            "suite": name,
            "generated_at": datetime.now(UTC).isoformat(),
            "encoder": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "dimensions": EMBEDDING_DIMENSION,
                "normalized": True,
            },
            "dataset": {
                "id": suite["dataset_id"],
                "memory_count": len(suite["memories"]),
                "should_admit_count": len(should_admit),
                "query_count": query_count,
            },
            "metrics": metrics,
            "metric_definitions": {
                "precision": "admission precision over labeled candidates",
                "recall": "admission recall over labeled candidates",
                "precision_at_k": (
                    "micro retrieval precision across all returned top-k items"
                ),
            },
            "queries": query_results,
        }
    finally:
        with get_connection() as conn, conn.transaction():
            conn.execute("SET LOCAL row_security = off")
            conn.execute("DELETE FROM memories WHERE tenant_id = %s", (tenant_id,))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a CMIS offline eval suite")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()

    report = evaluate_suite(args.suite)
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        report["comparison"] = compare_reports(report, baseline)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
