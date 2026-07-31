import argparse
import json
from pathlib import Path
from typing import Any


def render(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"Suite: {report['suite']}",
        (
            "Dataset: "
            f"{report['dataset']['memory_count']} memories, "
            f"{report['dataset']['query_count']} queries"
        ),
        f"Precision: {metrics['precision']:.3f}",
        f"Recall: {metrics['recall']:.3f}",
        f"Precision@{metrics['k']}: {metrics['precision_at_k']:.3f}",
    ]
    if "comparison" in report:
        lines.append(
            "Delta vs baseline: "
            + ", ".join(
                f"{name}={value:+.3f}"
                for name, value in report["comparison"].items()
            )
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Display a CMIS eval report")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    print(render(report))


if __name__ == "__main__":
    main()
