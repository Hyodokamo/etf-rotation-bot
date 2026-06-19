"""Tests for Phase 5.0.7: AI Sleeve 投入記録 (deployment log).

append-only monthly log; source-of-truth state CSV update; no order quantity.
All tests are hermetic (tmp_path; real data/ai_sleeve_state.csv never touched).
"""
import csv
from pathlib import Path

import pytest

from src.ai_sleeve_deployment_log import (
    DeploymentAction,
    DeploymentLogEntry,
    append_deployment_entry,
    archive_monthly_log,
    compute_new_sleeve_state,
    get_monthly_log_path,
    read_deployment_log,
    record_sleeve_deployment,
    update_ai_sleeve_state_csv,
)

_STATE_CSV_CONTENT = (
    "as_of_date,sleeve_name,total_budget_jpy,cash_jpy,invested_jpy,default_account,notes\n"
    "2026-06-05,ai_sleeve,1000000,1000000,0,taxable,0円スタート\n"
)


def _write_state(tmp_path: Path, content: str = _STATE_CSV_CONTENT) -> Path:
    p = tmp_path / "ai_sleeve_state.csv"
    p.write_text(content, encoding="utf-8")
    return p


def _entry(
    *,
    action: DeploymentAction = DeploymentAction.DEPLOY,
    symbol: str = "GRID",
    amount: float = 100_000,
    resulting_cash: float = 900_000,
    resulting_invested: float = 100_000,
) -> DeploymentLogEntry:
    return DeploymentLogEntry(
        as_of_date="2026-06-07",
        action=action,
        symbol=symbol,
        theme="smart_grid",
        consideration_jpy=amount,
        account="taxable",
        resulting_cash_jpy=resulting_cash,
        resulting_invested_jpy=resulting_invested,
        notes="test",
    )


# ── monthly log path ──────────────────────────────────────────────────────────


def test_deployment_log_monthly_path_format(tmp_path):
    p = get_monthly_log_path("2026-06", tmp_path)
    assert p.name == "ai_sleeve_state_202606.csv"
    assert p.parent == tmp_path


# ── append and read ───────────────────────────────────────────────────────────


def test_deployment_log_creates_monthly_file(tmp_path):
    log_path = get_monthly_log_path("2026-06", tmp_path)
    assert not log_path.exists()
    append_deployment_entry(_entry(), log_path)
    assert log_path.exists()


def test_deployment_log_appends_entry(tmp_path):
    log_path = get_monthly_log_path("2026-06", tmp_path)
    append_deployment_entry(_entry(symbol="GRID"), log_path)
    rows = read_deployment_log(log_path)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "GRID"
    assert rows[0]["action"] == "deploy"
    assert float(rows[0]["consideration_jpy"]) == 100_000


def test_deployment_log_multiple_entries_all_present(tmp_path):
    log_path = get_monthly_log_path("2026-06", tmp_path)
    append_deployment_entry(_entry(symbol="GRID"), log_path)
    append_deployment_entry(_entry(symbol="ITA", amount=50_000,
                                   resulting_cash=850_000, resulting_invested=150_000), log_path)
    rows = read_deployment_log(log_path)
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"GRID", "ITA"}


def test_deployment_log_missing_file_returns_empty(tmp_path):
    rows = read_deployment_log(tmp_path / "nope.csv")
    assert rows == []


# ── state arithmetic ──────────────────────────────────────────────────────────


def test_deployment_deploy_reduces_cash_increases_invested():
    new_cash, new_inv = compute_new_sleeve_state(
        current_cash=1_000_000, current_invested=0, total_budget=1_000_000,
        action=DeploymentAction.DEPLOY, amount_jpy=100_000,
    )
    assert new_inv == 100_000
    assert new_cash == 900_000


def test_deployment_reduce_increases_cash_decreases_invested():
    new_cash, new_inv = compute_new_sleeve_state(
        current_cash=900_000, current_invested=100_000, total_budget=1_000_000,
        action=DeploymentAction.REDUCE, amount_jpy=50_000,
    )
    assert new_inv == 50_000
    assert new_cash == 950_000


def test_deployment_note_action_no_amount_change():
    new_cash, new_inv = compute_new_sleeve_state(
        current_cash=900_000, current_invested=100_000, total_budget=1_000_000,
        action=DeploymentAction.NOTE, amount_jpy=99_999,
    )
    # NOTE must not change cash or invested
    assert new_cash == 900_000
    assert new_inv == 100_000


# ── state CSV update ──────────────────────────────────────────────────────────


def test_deployment_log_updates_sleeve_state_csv(tmp_path):
    state_path = _write_state(tmp_path)
    update_ai_sleeve_state_csv(
        state_path,
        as_of_date="2026-06-07",
        total_budget_jpy=1_000_000,
        new_cash_jpy=900_000,
        new_invested_jpy=100_000,
    )
    with open(state_path, encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert float(row["cash_jpy"]) == 900_000
    assert float(row["invested_jpy"]) == 100_000
    assert row["as_of_date"] == "2026-06-07"


# ── archive ───────────────────────────────────────────────────────────────────


def test_deployment_log_archive_copies_to_archive_dir(tmp_path):
    log_dir = tmp_path / "data"
    archive_dir = tmp_path / "archive"
    log_path = get_monthly_log_path("2026-06", log_dir)
    append_deployment_entry(_entry(), log_path)

    dst = archive_monthly_log("2026-06", log_dir=log_dir, archive_dir=archive_dir)
    assert dst is not None
    assert Path(dst).exists()
    assert Path(dst).name == "ai_sleeve_state_202606.csv"


# ── high-level record ─────────────────────────────────────────────────────────


def test_deployment_record_sleeve_deployment_end_to_end(tmp_path):
    state_path = _write_state(tmp_path)
    entry = record_sleeve_deployment(
        as_of_date="2026-06-07",
        action="deploy",
        symbol="GRID",
        theme="smart_grid",
        consideration_jpy=100_000,
        account="taxable",
        notes="テスト投入",
        state_path=state_path,
        log_dir=tmp_path,
        month="2026-06",
    )
    assert entry.resulting_cash_jpy == 900_000
    assert entry.resulting_invested_jpy == 100_000

    # log file created
    log_path = get_monthly_log_path("2026-06", tmp_path)
    rows = read_deployment_log(log_path)
    assert len(rows) == 1

    # state CSV updated
    with open(state_path, encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert float(row["cash_jpy"]) == 900_000
    assert float(row["invested_jpy"]) == 100_000


# ── safety invariants ─────────────────────────────────────────────────────────


def test_deployment_log_no_order_quantity_fields():
    # DeploymentLogEntry must have no quantity/shares/order fields.
    fields = set(DeploymentLogEntry.model_fields)
    for bad in ("quantity", "shares", "order_amount", "order_quantity", "num_shares"):
        assert bad not in fields, f"forbidden field present: {bad}"


def test_deployment_consideration_jpy_not_order_amount(tmp_path):
    # The field is named consideration_jpy, not order_amount.
    assert "consideration_jpy" in DeploymentLogEntry.model_fields
    assert "order_amount" not in DeploymentLogEntry.model_fields
    # The log row also uses 'consideration_jpy' as column name.
    log_path = get_monthly_log_path("2026-06", tmp_path)
    append_deployment_entry(_entry(), log_path)
    with open(log_path, encoding="utf-8") as f:
        header = f.readline()
    assert "consideration_jpy" in header
    assert "order_amount" not in header
    assert "quantity" not in header


def test_deployment_log_does_not_auto_deploy_from_candidate_review(tmp_path):
    # Candidate Review must NOT import or call record_sleeve_deployment.
    import importlib, inspect
    cr = importlib.import_module("src.committee.candidate_review")
    src_text = inspect.getsource(cr)
    assert "record_sleeve_deployment" not in src_text
    assert "ai_sleeve_deployment_log" not in src_text


def test_deployment_log_does_not_trigger_auto_trade(tmp_path):
    state_path = _write_state(tmp_path)
    entry = record_sleeve_deployment(
        as_of_date="2026-06-07",
        action="deploy",
        symbol="GRID",
        theme="smart_grid",
        consideration_jpy=100_000,
        state_path=state_path,
        log_dir=tmp_path,
        month="2026-06",
    )
    entry_dict = entry.model_dump()
    blob = str(entry_dict)
    for bad in ("auto_trade", "place_order", "execute_trade", "brokerage", "買え", "売れ"):
        assert bad not in blob
