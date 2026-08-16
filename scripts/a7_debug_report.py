from __future__ import annotations

import json
from pathlib import Path
import runpy
import tempfile

from analyzazprav.qa import validate_staging_dir, validate_vertical_pipeline


namespace = runpy.run_path("tests/test_a7_vertical.py")
build_vertical = namespace["_build_vertical"]

with tempfile.TemporaryDirectory() as tmp:
    try:
        staging, database, before, after = build_vertical(Path(tmp))
        staging_report = validate_staging_dir(staging)
        vertical_report = validate_vertical_pipeline(staging, database)
        payload = {
            "build": "ok",
            "fingerprint_equal": before == after,
            "staging_status": staging_report["status"],
            "staging_codes": [item["code"] for item in staging_report["issues"]],
            "staging_counts": staging_report["counts"],
            "vertical_status": vertical_report["status"],
            "vertical_codes": [item["code"] for item in vertical_report["issues"]],
            "vertical_checks": vertical_report["checks"],
        }
    except Exception as exc:
        payload = {
            "build": "exception",
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }

print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
