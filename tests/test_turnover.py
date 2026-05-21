import pytest

from src.turnover import apply_turnover_limit, compute_turnover, _blend


def test_zero_turnover_same_portfolio():
    w = {"A": 0.5, "B": 0.3, "C": 0.2}
    assert compute_turnover(w, w) == pytest.approx(0.0, abs=1e-9)


def test_full_rotation_turnover():
    prev = {"A": 1.0}
    new = {"B": 1.0}
    assert compute_turnover(new, prev) == pytest.approx(1.0, abs=1e-9)


def test_partial_turnover():
    prev = {"A": 0.6, "B": 0.4}
    new = {"A": 0.4, "B": 0.6}
    # Buy 0.2 of B
    assert compute_turnover(new, prev) == pytest.approx(0.2, abs=1e-9)


def test_limit_not_applied_when_under_limit():
    prev = {"A": 0.5, "B": 0.5}
    new = {"A": 0.6, "B": 0.4}
    result = apply_turnover_limit(new, prev, max_turnover=0.50)
    assert result == new


def test_limit_applied_when_over():
    prev = {"A": 1.0}
    new = {"B": 1.0}
    result = apply_turnover_limit(new, prev, max_turnover=0.30)
    actual_to = compute_turnover(result, prev)
    assert actual_to <= 0.30 + 1e-6


def test_blend_alpha_zero_returns_prev():
    prev = {"A": 0.5, "B": 0.5}
    new = {"B": 0.8, "C": 0.2}
    blended = _blend(new, prev, alpha=0.0)
    assert abs(blended.get("A", 0) - 0.5) < 1e-9
    assert abs(blended.get("B", 0) - 0.5) < 1e-9


def test_blend_alpha_one_returns_new():
    prev = {"A": 0.5, "B": 0.5}
    new = {"B": 0.8, "C": 0.2}
    blended = _blend(new, prev, alpha=1.0)
    assert abs(blended.get("B", 0) - 0.8) < 1e-9
    assert abs(blended.get("C", 0) - 0.2) < 1e-9


def test_result_weights_sum_to_one():
    prev = {"A": 0.4, "B": 0.3, "C": 0.3}
    new = {"B": 0.5, "D": 0.5}
    result = apply_turnover_limit(new, prev, max_turnover=0.20)
    assert abs(sum(result.values()) - 1.0) < 1e-6
