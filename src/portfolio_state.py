from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.logger import logger

STATE_FILE = Path("outputs/portfolio_state.json")


@dataclass
class PortfolioState:
    date: str
    weights: dict[str, float] = field(default_factory=dict)


def load_state(path: Path = STATE_FILE) -> PortfolioState | None:
    if not path.exists():
        logger.info("No previous portfolio state found")
        return None

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        state = PortfolioState(date=data["date"], weights=data["weights"])
        logger.info(f"Loaded previous portfolio state from {data['date']}")
        return state
    except Exception as e:
        logger.warning(f"Failed to load portfolio state: {e}")
        return None


def save_state(state: PortfolioState, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": state.date, "weights": state.weights}, f, ensure_ascii=False, indent=2)
    logger.info(f"Portfolio state saved to {path}")
