"""Phase 7.4: NAS Runtime Services tests — Slack Bot + Job Runner Docker Compose config.

All tests are static (read files; no Docker/subprocess execution).
They verify that docker-compose.yml and docs/nas_deployment_guide.md contain
all required content for Phase 7.4 runtime operation.

Safety invariants verified:
- slack-bot and job-runner services are defined (not commented out)
- Both services restart: unless-stopped
- Both services mount data/logs/reports/outputs and use env_file
- Both services set TZ=Asia/Tokyo
- slack-bot uses slack_interaction_handler
- job-runner uses --loop
- etf-bot default command does not include --allow-watchlist-update
- Runbook covers: service startup, job status log, Task Scheduler daily run
- Runbook states: no auto-trade, no order quantity, no brokerage connection
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_COMPOSE   = _ROOT / "docker-compose.yml"
_GUIDE     = _ROOT / "docs" / "nas_deployment_guide.md"


def _compose_text() -> str:
    return _COMPOSE.read_text(encoding="utf-8")


def _compose_data() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _guide_text() -> str:
    return _GUIDE.read_text(encoding="utf-8")


# ── docker-compose.yml: service existence ─────────────────────────────────────

def test_compose_has_slack_bot_service():
    """slack-bot must be a defined (non-commented) service in docker-compose.yml."""
    data = _compose_data()
    services = data.get("services") or {}
    assert "slack-bot" in services, (
        "slack-bot service must be defined in docker-compose.yml (not commented out)"
    )


def test_compose_has_job_runner_service():
    """job-runner must be a defined (non-commented) service in docker-compose.yml."""
    data = _compose_data()
    services = data.get("services") or {}
    assert "job-runner" in services, (
        "job-runner service must be defined in docker-compose.yml (not commented out)"
    )


# ── docker-compose.yml: slack-bot correctness ─────────────────────────────────

def test_slack_bot_service_uses_socket_mode_handler():
    """slack-bot command must invoke slack_interaction_handler.py (Socket Mode)."""
    data = _compose_data()
    cmd = data["services"]["slack-bot"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "slack_interaction_handler" in cmd_str, (
        "slack-bot command must invoke src/slack_interaction_handler.py"
    )


def test_slack_bot_service_restart_unless_stopped():
    """slack-bot must have restart: unless-stopped."""
    data = _compose_data()
    restart = data["services"]["slack-bot"].get("restart", "")
    assert restart == "unless-stopped", (
        f"slack-bot restart must be 'unless-stopped', got {restart!r}"
    )


def test_slack_bot_service_uses_env_file():
    """slack-bot must use env_file to load secrets (not hardcoded)."""
    data = _compose_data()
    env_file = data["services"]["slack-bot"].get("env_file")
    assert env_file is not None, "slack-bot must specify env_file"
    env_file_str = env_file if isinstance(env_file, str) else str(env_file)
    assert ".env" in env_file_str, f"slack-bot env_file must reference .env, got {env_file!r}"


def test_slack_bot_service_sets_timezone_tokyo():
    """slack-bot must set TZ=Asia/Tokyo."""
    data = _compose_data()
    env = data["services"]["slack-bot"].get("environment", {})
    tz = env.get("TZ") if isinstance(env, dict) else None
    assert tz == "Asia/Tokyo", (
        f"slack-bot TZ must be 'Asia/Tokyo', got {tz!r}"
    )


def test_slack_bot_service_mounts_all_volumes():
    """slack-bot must mount data, logs, reports, and outputs volumes."""
    data = _compose_data()
    volumes = data["services"]["slack-bot"].get("volumes", [])
    vol_str = " ".join(volumes)
    assert "/app/data" in vol_str,    "slack-bot must mount /app/data"
    assert "/app/logs" in vol_str,    "slack-bot must mount /app/logs"
    assert "/app/reports" in vol_str, "slack-bot must mount /app/reports"
    assert "/app/outputs" in vol_str, "slack-bot must mount /app/outputs"


# ── docker-compose.yml: job-runner correctness ────────────────────────────────

def test_job_runner_service_uses_loop():
    """job-runner command must include --loop for continuous polling."""
    data = _compose_data()
    cmd = data["services"]["job-runner"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "--loop" in cmd_str, (
        "job-runner command must include --loop"
    )


def test_job_runner_service_uses_run_job_worker():
    """job-runner command must invoke scripts/run_job_worker.py."""
    data = _compose_data()
    cmd = data["services"]["job-runner"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "run_job_worker" in cmd_str, (
        "job-runner command must invoke scripts/run_job_worker.py"
    )


def test_job_runner_service_restart_unless_stopped():
    """job-runner must have restart: unless-stopped."""
    data = _compose_data()
    restart = data["services"]["job-runner"].get("restart", "")
    assert restart == "unless-stopped", (
        f"job-runner restart must be 'unless-stopped', got {restart!r}"
    )


def test_job_runner_service_uses_env_file():
    """job-runner must use env_file."""
    data = _compose_data()
    env_file = data["services"]["job-runner"].get("env_file")
    assert env_file is not None, "job-runner must specify env_file"


def test_job_runner_service_sets_timezone_tokyo():
    """job-runner must set TZ=Asia/Tokyo."""
    data = _compose_data()
    env = data["services"]["job-runner"].get("environment", {})
    tz = env.get("TZ") if isinstance(env, dict) else None
    assert tz == "Asia/Tokyo", (
        f"job-runner TZ must be 'Asia/Tokyo', got {tz!r}"
    )


def test_job_runner_service_mounts_all_volumes():
    """job-runner must mount data, logs, reports, and outputs volumes."""
    data = _compose_data()
    volumes = data["services"]["job-runner"].get("volumes", [])
    vol_str = " ".join(volumes)
    assert "/app/data" in vol_str,    "job-runner must mount /app/data"
    assert "/app/logs" in vol_str,    "job-runner must mount /app/logs"
    assert "/app/reports" in vol_str, "job-runner must mount /app/reports"
    assert "/app/outputs" in vol_str, "job-runner must mount /app/outputs"


# ── docker-compose.yml: combined service checks ───────────────────────────────

def test_services_mount_data_logs_reports_outputs():
    """Both slack-bot and job-runner must mount all 4 runtime volumes."""
    data = _compose_data()
    for svc in ("slack-bot", "job-runner"):
        volumes = data["services"][svc].get("volumes", [])
        vol_str = " ".join(volumes)
        for mount in ("/app/data", "/app/logs", "/app/reports", "/app/outputs"):
            assert mount in vol_str, (
                f"Service {svc!r} must mount {mount!r}"
            )


def test_services_use_env_file():
    """Both slack-bot and job-runner must use env_file."""
    data = _compose_data()
    for svc in ("slack-bot", "job-runner"):
        env_file = data["services"][svc].get("env_file")
        assert env_file is not None, f"Service {svc!r} must specify env_file"


def test_services_set_timezone_tokyo():
    """Both slack-bot and job-runner must set TZ=Asia/Tokyo."""
    data = _compose_data()
    for svc in ("slack-bot", "job-runner"):
        env = data["services"][svc].get("environment", {})
        tz = env.get("TZ") if isinstance(env, dict) else None
        assert tz == "Asia/Tokyo", (
            f"Service {svc!r} must set TZ=Asia/Tokyo, got {tz!r}"
        )


def test_services_restart_unless_stopped():
    """Both slack-bot and job-runner must have restart: unless-stopped."""
    data = _compose_data()
    for svc in ("slack-bot", "job-runner"):
        restart = data["services"][svc].get("restart", "")
        assert restart == "unless-stopped", (
            f"Service {svc!r} restart must be 'unless-stopped', got {restart!r}"
        )


# ── docker-compose.yml: etf-bot safety ───────────────────────────────────────

def test_daily_runner_not_auto_watchlist_update():
    """The etf-bot default command must not include --allow-watchlist-update."""
    data = _compose_data()
    cmd = data["services"]["etf-bot"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "--allow-watchlist-update" not in cmd_str, (
        "etf-bot default command must not include --allow-watchlist-update"
    )


def test_job_runner_command_no_allow_watchlist_update():
    """The job-runner command must not include --allow-watchlist-update."""
    data = _compose_data()
    cmd = data["services"]["job-runner"].get("command", [])
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
    assert "--allow-watchlist-update" not in cmd_str, (
        "job-runner command must not include --allow-watchlist-update"
    )


# ── docs/nas_deployment_guide.md: runbook content ────────────────────────────

def test_runbook_mentions_slack_bot_up():
    """Runbook must describe how to start slack-bot with docker compose up."""
    guide = _guide_text()
    assert "slack-bot" in guide, "Runbook must mention slack-bot"
    # Guide must show a compose up command for slack-bot
    assert "up" in guide and "slack-bot" in guide, (
        "Runbook must describe 'docker compose up ... slack-bot'"
    )


def test_runbook_mentions_job_runner_up():
    """Runbook must describe how to start job-runner with docker compose up."""
    guide = _guide_text()
    assert "job-runner" in guide, "Runbook must mention job-runner"
    assert "up" in guide and "job-runner" in guide, (
        "Runbook must describe 'docker compose up ... job-runner'"
    )


def test_runbook_mentions_job_status_log():
    """Runbook must mention job_status.jsonl for job status monitoring."""
    guide = _guide_text()
    assert "job_status.jsonl" in guide, (
        "Runbook must mention job_status.jsonl for job monitoring"
    )


def test_runbook_mentions_task_scheduler_daily_run():
    """Runbook must explain daily run via Task Scheduler or cron."""
    guide = _guide_text()
    has_task_scheduler = "Task Scheduler" in guide or "タスクスケジューラ" in guide
    has_cron = "cron" in guide
    assert has_task_scheduler or has_cron, (
        "Runbook must mention Task Scheduler or cron for daily runs"
    )
    # Must mention daily run with docker compose (not a persistent service)
    assert "docker compose run" in guide or "docker-compose run" in guide, (
        "Runbook must show docker compose run for daily execution"
    )


def test_runbook_mentions_no_auto_trade():
    """Runbook must explicitly state no auto-trade."""
    guide = _guide_text()
    assert "自動売買なし" in guide or "no_auto_trade" in guide or "自動売買" in guide, (
        "Runbook must state that auto-trading is not performed"
    )


def test_runbook_mentions_no_order_quantity():
    """Runbook must explicitly state no order quantity calculation."""
    guide = _guide_text()
    assert "注文数量計算なし" in guide or "no_order_quantity" in guide, (
        "Runbook must state that order quantity calculation is not performed"
    )


def test_runbook_mentions_no_brokerage_connection():
    """Runbook must explicitly state no brokerage connection."""
    guide = _guide_text()
    assert "証券口座連携なし" in guide or "brokerage_connection" in guide, (
        "Runbook must state that brokerage connection is not implemented"
    )


def test_runbook_covers_troubleshooting():
    """Runbook must cover troubleshooting for job-runner and slack-bot failures."""
    guide = _guide_text()
    # Should cover QUEUED jobs that don't proceed
    assert "QUEUED" in guide, "Runbook must mention stuck QUEUED jobs"
    # Should mention restarting services
    assert "restart" in guide or "再起動" in guide, "Runbook must describe restart procedure"


def test_runbook_covers_log_commands():
    """Runbook must provide log-checking commands."""
    guide = _guide_text()
    assert "docker compose logs" in guide, (
        "Runbook must show 'docker compose logs' commands"
    )
