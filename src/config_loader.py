from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class AssetConfig(BaseModel):
    asset_id: str
    ticker: str
    display_name: str
    category: str
    include_stage: Literal["production", "watch"]


class UniverseConfig(BaseModel):
    assets: list[AssetConfig]


class MomentumWindowsConfig(BaseModel):
    mom_1m: int = 21
    mom_3m: int = 63
    mom_6m: int = 126
    mom_12m: int = 252


class MomentumWeightsConfig(BaseModel):
    mom_1m: float = 0.10
    mom_3m: float = 0.20
    mom_6m: float = 0.30
    mom_12m: float = 0.40


class ScoringConfig(BaseModel):
    momentum_windows: MomentumWindowsConfig = Field(default_factory=MomentumWindowsConfig)
    momentum_weights: MomentumWeightsConfig = Field(default_factory=MomentumWeightsConfig)
    trend_sma_windows: list[int] = Field(default_factory=lambda: [50, 200])
    trend_bonus_per_sma: float = 0.05
    volatility_window: int = 20
    vol_adjust: bool = True


class AllocationConfig(BaseModel):
    method: Literal["score_proportional", "equal_weight", "inverse_volatility"] = "score_proportional"
    top_n: int = 15
    min_assets: int = 5


class RiskConfig(BaseModel):
    max_weight_per_asset: float = 0.25
    min_weight_per_selected: float = 0.02
    max_category_weights: dict[str, float] = Field(default_factory=dict)
    risk_off_ticker: str = "VOO"
    risk_off_window: int = 60
    risk_off_threshold: float = -0.07
    risk_off_equity_cap: float = 0.40


class TurnoverConfig(BaseModel):
    max_turnover: float = 0.50


class ReportConfig(BaseModel):
    output_dir: str = "outputs"
    include_correlation_matrix: bool = True
    top_n_display: int = 15


class DataConfig(BaseModel):
    fetch_years: int = 3
    min_history_days: int = 260


class AiAuditConfig(BaseModel):
    enabled: bool = False
    model: str = "claude-3-5-sonnet-latest"
    apply_adjustment: bool = False


class AppConfig(BaseModel):
    universe: UniverseConfig
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    allocation: AllocationConfig = Field(default_factory=AllocationConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    turnover: TurnoverConfig = Field(default_factory=TurnoverConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    ai_audit: AiAuditConfig = Field(default_factory=AiAuditConfig)

    def production_assets(self) -> list[AssetConfig]:
        return [a for a in self.universe.assets if a.include_stage == "production"]

    def get_asset_by_ticker(self, ticker: str) -> AssetConfig | None:
        for a in self.universe.assets:
            if a.ticker == ticker:
                return a
        return None


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return AppConfig.model_validate(raw)
