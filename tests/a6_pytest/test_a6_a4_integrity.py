from __future__ import annotations

import sqlite3

import pytest

from a6.a4_integrity import require_reconciled
from a6.data import DataSourceError


def _connection(
    *,
    reconciliation_ok: int = 1,
    uses_latest_processing_run: int = 1,
    source_count: int = 3,
    processed_count: int = 3,
    accounted_count: int = 3,
    membership_delta: int = 0,
    invalid_response: int = 0,
    invalid_silence: int = 0,
    invalid_event: int = 0,
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE analysis_a4_reconciliation (
            conversation_id INTEGER,
            reconciliation_ok INTEGER,
            uses_latest_processing_run INTEGER,
            a4_source_membership_count INTEGER,
            a3_processed_membership_count INTEGER,
            membership_count_delta INTEGER,
            sender_accounted_membership_count INTEGER,
            invalid_response_session_count INTEGER,
            invalid_silence_session_count INTEGER,
            invalid_event_session_count INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO analysis_a4_reconciliation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            42,
            reconciliation_ok,
            uses_latest_processing_run,
            source_count,
            processed_count,
            membership_delta,
            accounted_count,
            invalid_response,
            invalid_silence,
            invalid_event,
        ),
    )
    return conn


def test_current_a4_reconciliation_contract_passes_when_all_invariants_hold():
    conn = _connection()
    try:
        require_reconciled(conn, ["42"], context="test")
    finally:
        conn.close()


def test_current_a4_reconciliation_rejects_stale_processing_run():
    conn = _connection(uses_latest_processing_run=0, reconciliation_ok=0)
    try:
        with pytest.raises(DataSourceError, match="latest A3 processing run"):
            require_reconciled(conn, ["42"], context="test")
    finally:
        conn.close()


def test_current_a4_reconciliation_rejects_membership_accounting_mismatch():
    conn = _connection(processed_count=2, membership_delta=1, reconciliation_ok=0)
    try:
        with pytest.raises(DataSourceError, match="membership_count_delta=0"):
            require_reconciled(conn, ["42"], context="test")
    finally:
        conn.close()


def test_current_a4_reconciliation_rejects_invalid_session_provenance():
    conn = _connection(invalid_event=1, reconciliation_ok=0)
    try:
        with pytest.raises(DataSourceError, match="invalid_event_session_count=0"):
            require_reconciled(conn, ["42"], context="test")
    finally:
        conn.close()


def test_legacy_reconciliation_view_remains_supported():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE analysis_a4_reconciliation (conversation_id INTEGER, reconciliation_ok INTEGER)"
    )
    conn.execute("INSERT INTO analysis_a4_reconciliation VALUES (42, 1)")
    try:
        require_reconciled(conn, ["42"], context="legacy")
    finally:
        conn.close()
