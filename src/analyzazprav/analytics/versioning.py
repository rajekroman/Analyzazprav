from __future__ import annotations

import hashlib
import json

from .config import AnalyticsConfig

ANALYTICS_VERSION = "9"


def analysis_signature(config: AnalyticsConfig) -> str:
    """Hash algorithm version, input contract and deterministic configuration."""

    payload = {
        "analytics_version": ANALYTICS_VERSION,
        "input_contract": "a2-membership+a3-processing-run+resolved-participant-v2",
        "topic_method": "lexical_ngram_v1",
        "topic_marker_method": "topic_marker_cooccurrence_v1",
        "config": config.as_dict(),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
