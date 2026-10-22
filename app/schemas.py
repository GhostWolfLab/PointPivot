"""Pydantic schemas for API request/response"""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


# ──────────── Strategy ────────────

class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    enabled: bool = False
    ai_provider: str = ""
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    prompt_template: str
    symbols: list[str] = []
    interval_minutes: int = Field(15, ge=1, le=1440)
    position_usdt: float = Field(100.0, ge=1)       # Fixed USDT notional per trade
    max_position_pct: float = Field(10.0, ge=0.1, le=100)  # legacy
    leverage: int = Field(3, ge=1, le=125)
    take_profit_pct: float = Field(3.0, ge=0.1, le=100)    # legacy
    stop_loss_pct: float = Field(2.0, ge=0.1, le=100)      # legacy
    scan_enabled: bool = False
    scan_top_n: int = Field(5, ge=1, le=50)
    scan_min_change_pct: float = Field(3.0, ge=0.5, le=100)
    scan_min_volume_usdt: float = Field(500000, ge=0)
    scan_exclude_symbols: list[str] = []
    use_ai: bool = True


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    ai_provider: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None
    prompt_template: Optional[str] = None
    symbols: Optional[list[str]] = None
    interval_minutes: Optional[int] = None
    position_usdt: Optional[float] = None
    max_position_pct: Optional[float] = None
    leverage: Optional[int] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    scan_enabled: Optional[bool] = None
    scan_top_n: Optional[int] = None
    scan_min_change_pct: Optional[float] = None
    scan_min_volume_usdt: Optional[float] = None
    scan_exclude_symbols: Optional[list[str]] = None
    use_ai: Optional[bool] = None


class StrategyOut(BaseModel):
    id: int
    name: str
    enabled: bool
    ai_provider: str
    ai_base_url: str
    ai_model: str
    prompt_template: str
    symbols: list[str]
    interval_minutes: int
    position_usdt: float
    max_position_pct: float
    leverage: int
    take_profit_pct: float
    stop_loss_pct: float
    scan_enabled: bool
    scan_top_n: int
    scan_min_change_pct: float
    scan_min_volume_usdt: float
    scan_exclude_symbols: list[str]
    use_ai: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────── Strategy Log ────────────

class StrategyLogOut(BaseModel):
    id: int
    strategy_id: int
    symbol: str
    prompt_sent: str
    ai_raw_response: str
    parsed_action: str
    parsed_confidence: int
    parsed_reasoning: str
    order_placed: bool
    order_id: str
    error_message: str
    created_at: datetime

    class Config:
        from_attributes = True


# ──────────── Trade ────────────

class TradeOut(BaseModel):
    id: int
    strategy_id: int
    symbol: str
    side: str
    action: str
    price: float
    quantity: float
    order_id: str
    tp_price: Optional[float]
    sl_price: Optional[float]
    pnl: Optional[float]
    fee: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ──────────── Position ────────────

class PositionOut(BaseModel):
    id: int
    strategy_id: Optional[int]
    symbol: str
    side: str
    size: float
    avg_price: float
    unrealized_pnl: float
    leverage: int
    margin: float
    tp_price: Optional[float]
    sl_price: Optional[float]
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────── Prompt Templates ────────────

class PromptTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    content: str = Field(..., min_length=1)
    is_default: bool = False


class PromptTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    is_default: Optional[bool] = None


class PromptTemplateOut(BaseModel):
    id: int
    name: str
    description: str
    content: str
    is_default: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ──────────── System Config ────────────

class SystemConfigUpdate(BaseModel):
    okx_live_api_key: Optional[str] = None
    okx_live_secret_key: Optional[str] = None
    okx_live_passphrase: Optional[str] = None
    okx_sim_api_key: Optional[str] = None
    okx_sim_secret_key: Optional[str] = None
    okx_sim_passphrase: Optional[str] = None
    okx_simulated: Optional[bool] = None
    okx_proxy: Optional[str] = None
    ai_default_provider: Optional[str] = None
    ai_default_api_key: Optional[str] = None
    ai_default_base_url: Optional[str] = None
    ai_default_model: Optional[str] = None
    tg_bot_token: Optional[str] = None
    tg_chat_id: Optional[str] = None
    tg_proxy: Optional[str] = None


class SystemConfigOut(BaseModel):
    okx_live_api_key_set: bool
    okx_live_secret_key_set: bool
    okx_live_passphrase_set: bool
    okx_sim_api_key_set: bool
    okx_sim_secret_key_set: bool
    okx_sim_passphrase_set: bool
    okx_simulated: bool
    okx_proxy: str
    ai_default_provider: str
    ai_default_api_key_set: bool
    ai_default_base_url: str
    ai_default_model: str
    tg_bot_token_set: bool
    tg_chat_id: str
    tg_proxy: str
    updated_at: Optional[datetime]


# ──────────── Equity ────────────

class EquitySnapshotOut(BaseModel):
    total_equity: float
    available_balance: float
    timestamp: datetime

    class Config:
        from_attributes = True


# ──────────── Manual Actions ────────────

class ManualCloseRequest(BaseModel):
    symbol: str
    side: str  # long / short


class RunStrategyRequest(BaseModel):
    strategy_id: int
