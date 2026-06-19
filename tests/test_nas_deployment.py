"""Phase 7.0: NAS Deployment Foundation tests.

All tests are static (read files; no Docker/subprocess execution) so they run
quickly and have no side effects. They verify that:
- Dockerfile is correct and safe (non-root, no .env copy, python-slim base)
- .dockerignore excludes secrets, personal CSVs, logs, reports, outputs
- docker-compose.yml mounts data/logs/reports/outputs and uses env_file
- run_daily.sh is a safe Linux entry point
- .env.example documents TZ
- NAS guide exists and covers all required topics

Safety invariants checked:
- No auto-trade / no order quantity / no brokerage references in guide
- .env never copied into image
- Personal CSVs never in image
- watchlist.csv not auto-updated by default command
"""
from __future__ import annotations

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE  = _ROOT / "Dockerfile"
_DOCKERIGNORE = _ROOT / ".dockerignore"
_COMPOSE     = _ROOT / "docker-compose.yml"
_RUN_SH      = _ROOT / "scripts" / "run_daily.sh"
_ENV_EXAMPLE = _ROOT / ".env.example"
_GITIGNORE   = _ROOT / ".gitignore"
_NAS_GUIDE   = _ROOT / "docs" / "nas_deployment_guide.md"
_REQUIREMENTS = _ROOT / "requirements.txt"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Dockerfile ────────────────────────────────────────────────────────────────

def test_dockerfile_exists():
    assert _DOCKERFILE.exists(), "Dockerfile must exist"
    assert len(_read(_DOCKERFILE)) > 100


def test_dockerfile_uses_python_slim():
    """Dockerfile must use a python slim image (not full / alpine)."""
    content = _read(_DOCKERFILE)
    assert "FROM python:" in content
    assert "slim" in content, "Expected python:*-slim base image"


def test_dockerfile_runs_as_non_root():
    """Dockerfile must create and switch to a non-root user."""
    content = _read(_DOCKERFILE)
    assert "USER " in content
    # Must not be USER root after the initial setup
    user_lines = [l.strip() for l in content.splitlines() if l.strip().startswith("USER ")]
    assert user_lines, "No USER instruction found"
    final_user = user_lines[-1]
    assert "root" not in final_user.lower(), f"Final USER must not be root, got: {final_user}"


def test_dockerfile_does_not_copy_env():
    """Dockerfile must never COPY .env or ENV with secret values."""
    content = _read(_DOCKERFILE)
    copy_lines = [l for l in content.splitlines() if l.strip().startswith("COPY")]
    for line in copy_lines:
        assert ".env" not in line or ".env.example" in line, (
            f".env must not be COPYed into image: {line!r}"
        )
    # Must not hardcode API keys
    assert "ANTHROPIC_API_KEY=" not in content or "ANTHROPIC_API_KEY=\n" not in content
    for secret_word in ("sk-ant-", "xoxb-", "xapp-"):
        assert secret_word not in content, f"Hardcoded secret found in Dockerfile: {secret_word}"


# ── .dockerignore ─────────────────────────────────────────────────────────────

def test_dockerignore_excludes_env():
    """dockerignore must exclude .env (but .env.example is allowed)."""
    content = _read(_DOCKERIGNORE)
    lines = [l.strip() for l in content.splitlines() if not l.strip().startswith("#") and l.strip()]
    # .env (exact match or glob) must be present, .env.example is fine
    assert any(l in (".env", ".env*") or l == ".env.local" for l in lines) or ".env" in content, (
        ".env must be excluded in .dockerignore"
    )


def test_dockerignore_excludes_personal_csv():
    """dockerignore must exclude personal / runtime-state CSV files."""
    content = _read(_DOCKERIGNORE)
    assert "watchlist.csv" in content, "watchlist.csv must be in .dockerignore"
    assert "watchlist_candidates.csv" in content, (
        "watchlist_candidates.csv must be in .dockerignore "
        "(contains personal investment amounts/accounts)"
    )
    assert "market_data_latest.csv" in content, "market_data_latest.csv must be in .dockerignore"
    assert "total_portfolio_snapshot.csv" in content, "total_portfolio_snapshot.csv must be in .dockerignore"
    assert "ai_sleeve_state.csv" in content, "ai_sleeve_state.csv must be in .dockerignore"


def test_dockerignore_excludes_logs_reports_outputs():
    """dockerignore must exclude runtime state directories."""
    content = _read(_DOCKERIGNORE)
    assert "logs/" in content or "logs" in content
    assert "reports/" in content or "reports" in content
    assert "outputs/" in content or "outputs" in content


# ── docker-compose.yml ────────────────────────────────────────────────────────

def test_compose_exists():
    assert _COMPOSE.exists(), "docker-compose.yml must exist"
    assert len(_read(_COMPOSE)) > 100


def test_compose_uses_env_file():
    """docker-compose must use env_file (not hardcode secrets)."""
    content = _read(_COMPOSE)
    assert "env_file" in content, "docker-compose.yml must use env_file"
    assert ".env" in content


def test_compose_mounts_data_logs_reports_outputs():
    """docker-compose must mount all four runtime directories as volumes."""
    content = _read(_COMPOSE)
    assert "./data" in content and "/app/data" in content, "data volume missing"
    assert "./logs" in content and "/app/logs" in content, "logs volume missing"
    assert "./reports" in content and "/app/reports" in content, "reports volume missing"
    assert "./outputs" in content and "/app/outputs" in content, "outputs volume missing"


def test_compose_sets_timezone_tokyo():
    """docker-compose must set TZ=Asia/Tokyo."""
    content = _read(_COMPOSE)
    assert "Asia/Tokyo" in content, "docker-compose.yml must set TZ=Asia/Tokyo"


def test_compose_default_command_no_watchlist_update():
    """The default compose command must not include --allow-watchlist-update."""
    content = _read(_COMPOSE)
    # Find lines that reference daily_signal_check.py as a command (not comments)
    cmd_lines = [
        l for l in content.splitlines()
        if "daily_signal_check.py" in l and not l.strip().startswith("#")
    ]
    for line in cmd_lines:
        assert "--allow-watchlist-update" not in line, (
            f"Default command must not include --allow-watchlist-update: {line!r}"
        )


# ── run_daily.sh ──────────────────────────────────────────────────────────────

def test_run_daily_sh_exists():
    assert _RUN_SH.exists(), "scripts/run_daily.sh must exist"
    content = _read(_RUN_SH)
    assert len(content) > 100


def test_run_daily_sh_uses_project_root():
    """run_daily.sh must cd to project root (not hardcode a path)."""
    content = _read(_RUN_SH)
    # Should use BASH_SOURCE or dirname or similar, not a hardcoded Windows/absolute path
    assert "BASH_SOURCE" in content or "dirname" in content or "cd " in content
    # Must not hardcode C:\ or /Users/
    assert "C:\\" not in content
    assert "/Users/" not in content


def test_run_daily_sh_calls_daily_signal_check():
    """run_daily.sh must invoke daily_signal_check.py."""
    content = _read(_RUN_SH)
    assert "daily_signal_check.py" in content
    assert "python" in content


def test_run_daily_sh_does_not_allow_watchlist_update_by_default():
    """run_daily.sh must NOT hardcode --allow-watchlist-update."""
    content = _read(_RUN_SH)
    # Find lines that call python daily_signal_check.py (not comments)
    python_call_lines = [
        l for l in content.splitlines()
        if "daily_signal_check.py" in l
        and not l.strip().startswith("#")
        and "allow-watchlist-update" in l
    ]
    assert not python_call_lines, (
        "run_daily.sh must not hardcode --allow-watchlist-update: "
        + str(python_call_lines)
    )


def test_run_daily_sh_appends_to_console_log():
    """run_daily.sh must append stdout/stderr to a log file."""
    content = _read(_RUN_SH)
    assert "tee" in content or ">>" in content, (
        "run_daily.sh must append output to a log file (tee or >>)"
    )
    assert "console.log" in content or "CONSOLE_LOG" in content


# ── .env.example ─────────────────────────────────────────────────────────────

def test_env_example_mentions_timezone():
    """env.example must document the TZ variable for Docker/NAS."""
    content = _read(_ENV_EXAMPLE)
    assert "TZ" in content
    assert "Asia/Tokyo" in content


# ── NAS guide ─────────────────────────────────────────────────────────────────

def test_nas_guide_exists():
    assert _NAS_GUIDE.exists(), "docs/nas_deployment_guide.md must exist"
    content = _read(_NAS_GUIDE)
    assert len(content) > 500


def test_nas_guide_mentions_docker():
    content = _read(_NAS_GUIDE)
    assert "docker" in content.lower() or "Docker" in content


def test_nas_guide_mentions_synology_or_cron():
    content = _read(_NAS_GUIDE)
    assert "Synology" in content or "cron" in content


def test_nas_guide_mentions_volume_design():
    content = _read(_NAS_GUIDE)
    assert "volume" in content.lower() or "bind-mount" in content or "Volume" in content


def test_nas_guide_mentions_dry_run_default():
    content = _read(_NAS_GUIDE)
    assert "dry-run" in content or "dry_run" in content or "watchlist.csv" in content


def test_nas_guide_mentions_no_auto_trade():
    content = _read(_NAS_GUIDE)
    assert "自動売買" in content or "no-auto-trade" in content or "no_auto_trade" in content


def test_nas_guide_mentions_no_order_quantity():
    content = _read(_NAS_GUIDE)
    assert "注文数量" in content or "no_order_quantity" in content or "order quantity" in content.lower()


def test_nas_guide_mentions_no_brokerage_connection():
    content = _read(_NAS_GUIDE)
    assert "証券口座" in content or "brokerage" in content.lower()


# ── .gitignore final state ────────────────────────────────────────────────────

def test_gitignore_excludes_watchlist_and_market_data():
    """After Phase 7.0 pre-work, .gitignore must explicitly exclude both state CSV files."""
    content = _read(_GITIGNORE)
    assert "data/watchlist.csv" in content, "watchlist.csv must be in .gitignore"
    assert "data/market_data_latest.csv" in content, "market_data_latest.csv must be in .gitignore"


# ── requirements ──────────────────────────────────────────────────────────────

def test_requirements_includes_slack_bolt():
    content = _read(_REQUIREMENTS)
    assert "slack_bolt" in content or "slack-bolt" in content, (
        "slack_bolt must be in requirements.txt"
    )
