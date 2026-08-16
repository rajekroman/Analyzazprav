from __future__ import annotations

import hashlib
import json

from .config import AnalyticsConfig

ANALYTICS_VERSION = "6"


def analysis_signature(config: AnalyticsConfig) -> str:
    """Hash code/schema version plus all analysis configuration.

    Source fingerprints intentionally describe source data only. This separate
    signature ensures incremental mode also reruns when deterministic analysis
    rules or thresholds change.
    """

    payload = {
        "analytics_version": ANALYTICS_VERSION,
        "topic_method": "lexical_ngram_v1",
        "config": config.as_dict(),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
