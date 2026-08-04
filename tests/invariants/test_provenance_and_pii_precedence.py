import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.domain.memory import MemoryType
from app.domain.policy import PiiStatus, payload_from_request
from worker import llm_providers
from worker.write_gate import evaluate_candidate

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "app" / "api"


def _worker_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("worker."):
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("worker."):
                offenders.append(f"{path.name}:{node.lineno} from {module}")
    return offenders


def test_api_imports_only_worker_queue() -> None:
    offenders: list[str] = []
    forbidden = {
        "worker.write_gate",
        "worker.decay_job",
        "worker.reflection_agent",
        "worker.pii_filter",
        "worker.llm_providers",
    }
    for path in sorted(API_DIR.rglob("*.py")):
        for offender in _worker_imports(path):
            module = offender.split(" from ", 1)[-1]
            if module in forbidden:
                offenders.append(offender)
    assert not offenders, (
        "app/api/** must not import write-gate internals directly:\n"
        + "\n".join(offenders)
    )


def test_pii_filter_precedes_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _spy(_content: str) -> None:
        calls.append("judge")

    monkeypatch.setattr(llm_providers, "judge_with_fallback", _spy)

    payload = payload_from_request(
        tenant_id=uuid4(),
        user_id=uuid4(),
        memory_type=MemoryType.SEMANTIC,
        content="Reach me at 555-123-4567 anytime.",
        source_turn_ids=[uuid4()],
        importance=0.5,
    )
    result = evaluate_candidate(payload)

    assert result["outcome"] == "rejected"
    assert result["reason"] == "pii_blocked"
    assert calls == []


def test_admitted_rows_carry_provenance_and_pii_verdict() -> None:
    payload = payload_from_request(
        tenant_id=uuid4(),
        user_id=uuid4(),
        memory_type=MemoryType.SEMANTIC,
        content="The user prefers handwritten notes.",
        source_turn_ids=[uuid4()],
        importance=0.5,
    )
    result = evaluate_candidate(payload)
    assert result["outcome"] == "admitted"
    trace = result["trace"]
    assert trace["source_turn_ids"]
    assert trace["pii_verdict"]["verdict_id"]
    assert trace["pii_verdict"]["status"] == PiiStatus.CLEAN.value


def test_worker_providers_declare_timeouts() -> None:
    source = (ROOT / "worker" / "llm_providers.py").read_text(encoding="utf-8")
    assert "timeout" in source
    assert "write_gate_provider_timeout_seconds" in source
