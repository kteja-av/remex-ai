import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

READ_PATH_ENTRYPOINTS = (
    ROOT / "app" / "api" / "routes_retrieve.py",
    ROOT / "app" / "context" / "budgeter.py",
    ROOT / "app" / "retrieval" / "vector.py",
    ROOT / "app" / "embedding" / "local_encoder.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "worker",
    "httpx",
    "requests",
    "aiohttp",
    "urllib",
    "openai",
    "google",
    "anthropic",
)


def _import_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_read_path_modules_do_not_import_worker_or_http_clients() -> None:
    offenders: list[str] = []
    for path in READ_PATH_ENTRYPOINTS:
        for name in _import_names(path):
            if name == "worker" or name.startswith("worker."):
                offenders.append(f"{path.name}: {name}")
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                if name == prefix or name.startswith(f"{prefix}."):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, (
        "read path must stay local-only (no worker or HTTP client imports):\n"
        + "\n".join(offenders)
    )


def test_local_encoder_loads_baked_model_only() -> None:
    source = (ROOT / "app" / "embedding" / "local_encoder.py").read_text(
        encoding="utf-8"
    )
    assert "local_files_only=True" in source


def test_retrieve_route_wires_budgeter() -> None:
    module = importlib.import_module("app.api.routes_retrieve")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "app.context.budgeter" in source
    assert "worker" not in source
