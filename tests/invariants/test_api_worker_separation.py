import ast
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"


def _worker_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "worker" or alias.name.startswith("worker."):
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "worker" or module.startswith("worker."):
                offenders.append(f"{path.name}:{node.lineno} from {module}")
    return offenders


def test_api_never_imports_worker() -> None:
    offenders: list[str] = []
    for path in sorted(API_DIR.rglob("*.py")):
        offenders.extend(_worker_imports(path))
    assert not offenders, (
        "app/api/** must not import worker/** "
        "(background_jobs_never_block_the_request_path):\n" + "\n".join(offenders)
    )


def test_api_and_worker_are_separate_services() -> None:
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert "app.api.main:app" in compose, "api service must serve the FastAPI app"
    assert "worker.main" in compose, "worker service must run the worker entrypoint"
