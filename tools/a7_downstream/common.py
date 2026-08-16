from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


def load_downstream_validator(repo_root: str | Path = ".") -> ModuleType:
    path = Path(repo_root).resolve() / "src" / "analyzazprav" / "qa" / "downstream.py"
    spec = importlib.util.spec_from_file_location("a7_downstream_validator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load A7 downstream validator from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_report(report: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(destination.read_text(encoding="utf-8"), end="")
