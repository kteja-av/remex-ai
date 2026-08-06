import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.domain.policy import payload_from_request
from app.db.session import get_connection
from app.domain.memory import MemoryType
from app.embedding.local_encoder import EMBEDDING_DIMENSION, get_encoder
from app.retrieval.hybrid import retrieve_hybrid
from app.retrieval.vector import retrieve_similar, store_memory
from worker.write_gate import evaluate_candidate

Retriever = Callable[..., list[Any]]


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
        "precision": correctly_admitted / len(admitted) if admitted else 0.0,
        "recall": (
            correctly_admitted / len(should_admit) if should_admit else 0.0
        ),
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
        if "vector_only_on_suite" not in report:
            raise ValueError(
                "cannot compare reports from different labeled datasets: "
                f"{current_id!r} != {baseline_id!r}"
            )
        return {
            "basis": "vector_only_on_suite",
            "precision_at_k": (
                report["metrics"]["precision_at_k"]
                - report["vector_only_on_suite"]["metrics"]["precision_at_k"]
            ),
        }
    return {
        metric: report["metrics"][metric] - baseline["metrics"][metric]
        for metric in ("precision", "recall", "precision_at_k")
    }


def _run_queries(
    *,
    suite: dict[str, Any],
    tenant_id: UUID,
    user_id: UUID,
    labels_by_id: dict[str, str],
    retriever: Retriever,
    encoder: Any,
) -> list[dict[str, Any]]:
    k = int(suite["k"])
    query_results: list[dict[str, Any]] = []
    for query in suite["queries"]:
        query_text = query["text"]
        if retriever is retrieve_similar:
            hits = retriever(
                tenant_id=tenant_id,
                user_id=user_id,
                query_embedding=encoder.encode(query_text),
                limit=k,
            )
            hit_list = hits
        else:
            hits = retriever(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query_text,
                query_embedding=encoder.encode(query_text),
                limit=k,
            )
            hit_list = hits.hits
        retrieved = [labels_by_id[str(hit.memory.id)] for hit in hit_list]
        relevant = set(query["relevant"])
        matched = len(relevant.intersection(retrieved))
        query_results.append(
            {
                "query": query_text,
                "relevant": sorted(relevant),
                "retrieved": retrieved,
                "matched": matched,
            }
        )
    return query_results


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
        retriever: Retriever = (
            retrieve_hybrid if name == "hybrid" else retrieve_similar
        )
        query_results = _run_queries(
            suite=suite,
            tenant_id=tenant_id,
            user_id=user_id,
            labels_by_id=labels_by_id,
            retriever=retriever,
            encoder=encoder,
        )

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
        report: dict[str, Any] = {
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
                "query_count": len(suite["queries"]),
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
        if name == "hybrid":
            vector_query_results = _run_queries(
                suite=suite,
                tenant_id=tenant_id,
                user_id=user_id,
                labels_by_id=labels_by_id,
                retriever=retrieve_similar,
                encoder=encoder,
            )
            vector_metrics = compute_metrics(
                admitted=admitted,
                should_admit=should_admit,
                query_results=vector_query_results,
                k=k,
            )
            report["vector_only_on_suite"] = {
                "metrics": vector_metrics,
                "queries": vector_query_results,
            }
            report["comparison_on_suite"] = {
                "basis": "vector_only_on_suite",
                "precision_at_k": (
                    metrics["precision_at_k"] - vector_metrics["precision_at_k"]
                ),
            }
        return report
    finally:
        with get_connection() as conn, conn.transaction():
            conn.execute("SET LOCAL row_security = off")
            conn.execute(
                "SELECT set_config('app.bypass_audit_immutability', 'on', true)"
            )
            conn.execute(
                "DELETE FROM memory_audit WHERE tenant_id = %s", (tenant_id,)
            )
            conn.execute(
                "DELETE FROM memory_entity_links WHERE tenant_id = %s", (tenant_id,)
            )
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
