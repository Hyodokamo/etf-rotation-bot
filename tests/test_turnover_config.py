"""Tests for Phase 2.3: TurnoverConfig normal / migration separation."""
import pytest

from src.config_loader import TurnoverConfig


def test_normal_mode_uses_normal_limit():
    cfg = TurnoverConfig(normal_limit=0.20, migration_limit=0.50, migration_mode=False)
    assert cfg.effective_limit == pytest.approx(0.20)


def test_migration_mode_uses_migration_limit():
    cfg = TurnoverConfig(normal_limit=0.20, migration_limit=0.50, migration_mode=True)
    assert cfg.effective_limit == pytest.approx(0.50)


def test_legacy_max_turnover_overrides():
    """Deprecated max_turnover takes priority over normal/migration limits."""
    cfg = TurnoverConfig(
        normal_limit=0.20, migration_limit=0.50,
        migration_mode=False, max_turnover=0.30,
    )
    assert cfg.effective_limit == pytest.approx(0.30)


def test_legacy_max_turnover_overrides_in_migration_mode():
    cfg = TurnoverConfig(
        normal_limit=0.20, migration_limit=0.50,
        migration_mode=True, max_turnover=0.35,
    )
    assert cfg.effective_limit == pytest.approx(0.35)


def test_default_normal_limit_is_20pct():
    cfg = TurnoverConfig()
    assert cfg.normal_limit == pytest.approx(0.20)


def test_default_migration_limit_is_50pct():
    cfg = TurnoverConfig()
    assert cfg.migration_limit == pytest.approx(0.50)


def test_default_mode_is_normal():
    cfg = TurnoverConfig()
    assert cfg.migration_mode is False


def test_mode_label_normal():
    cfg = TurnoverConfig(migration_mode=False)
    assert cfg.mode_label == "通常運用"


def test_mode_label_migration():
    cfg = TurnoverConfig(migration_mode=True)
    assert cfg.mode_label == "移行運用"


def test_max_turnover_none_by_default():
    cfg = TurnoverConfig()
    assert cfg.max_turnover is None


def test_effective_limit_with_no_fields():
    """Default TurnoverConfig: no max_turnover, no migration → normal_limit."""
    cfg = TurnoverConfig()
    assert cfg.effective_limit == pytest.approx(cfg.normal_limit)
