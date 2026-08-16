from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CSV_MAPPING_PROFILE_VERSION = "1"
CANONICAL_FIELDS = frozenset(
    {
        "id",
        "guid",
        "conversation",
        "sender",
        "timestamp",
        "text",
        "service",
        "direction",
        "attachment",
    }
)


@dataclass(frozen=True, slots=True)
class CSVMappingProfile:
    version: str
    delimiter: str
    has_header: bool
    fields: dict[str, str | int]

    @classmethod
    def load(cls, path: Path) -> "CSVMappingProfile":
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid CSV mapping profile JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("CSV mapping profile must be a JSON object")

        version = str(value.get("version") or "")
        if version != CSV_MAPPING_PROFILE_VERSION:
            raise ValueError(f"Unsupported CSV mapping profile version: {version!r}")

        delimiter = value.get("delimiter")
        if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in {"\r", "\n"}:
            raise ValueError("CSV mapping profile delimiter must be exactly one non-newline character")

        has_header = value.get("has_header")
        if not isinstance(has_header, bool):
            raise ValueError("CSV mapping profile has_header must be true or false")

        raw_fields = value.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            raise ValueError("CSV mapping profile fields must be a non-empty object")

        unknown = sorted(set(str(key) for key in raw_fields) - CANONICAL_FIELDS)
        if unknown:
            raise ValueError(f"Unsupported canonical CSV mapping field(s): {', '.join(unknown)}")

        fields: dict[str, str | int] = {}
        for raw_field, selector in raw_fields.items():
            field = str(raw_field)
            if has_header:
                if not isinstance(selector, str) or not selector:
                    raise ValueError(
                        f"Headered CSV mapping for {field!r} must be a non-empty source column name"
                    )
                fields[field] = selector
            else:
                if isinstance(selector, bool) or not isinstance(selector, int) or selector < 0:
                    raise ValueError(
                        f"Headerless CSV mapping for {field!r} must be a zero-based non-negative column index"
                    )
                fields[field] = selector

        return cls(
            version=version,
            delimiter=delimiter,
            has_header=has_header,
            fields=fields,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "delimiter": self.delimiter,
            "has_header": self.has_header,
            "fields": dict(self.fields),
        }

    def semantic_sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def manifest_metadata(
        self,
        *,
        profile_name: str,
        file_sha256: str,
    ) -> dict[str, Any]:
        return {
            "mapping_profile": {
                **self.canonical_payload(),
                "name": profile_name,
                "file_sha256": file_sha256,
                "semantic_sha256": self.semantic_sha256(),
            }
        }
