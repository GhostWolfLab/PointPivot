"""Database models and initialization"""

import json
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime,
    ForeignKey, create_engine, event
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = "sqlite:///trading.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Enable WAL mode for better concurrent access
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ──────────────────── Models ────────────────────

class SystemConfig(Base):
    """Global system configuration (single row)"""
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, default=1)
    # 实盘凭证
    okx_api_key_enc = Column(Text, default="")
    okx_secret_key_enc = Column(Text, default="")
    okx_passphrase_enc = Column(Text, default="")
    # 模拟盘凭证
    okx_sim_api_key_enc = Column(Text, default="")
    okx_sim_secret_key_enc = Column(Text, default="")
    okx_sim_passphrase_enc = Column(Text, default="")
    okx_simulated = Column(Boolean, default=True)
    okx_proxy = Column(String(500), default="")

    ai_default_provider = Column(String(50), default="deepseek")
    ai_default_api_key_enc = Column(Text, default="")
    ai_default_base_url = Column(String(500), default="https://api.deepseek.com/v1")
    ai_default_model = Column(String(100), default="deepseek-chat")

    # Telegram notification (open position only)
    tg_bot_token_enc = Column(Text, default="")
    tg_chat_id = Column(String(200), default="")
    tg_proxy = Column(String(500), default="")

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Strategy(Base):
    """AI trading strategy"""
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    enabled = Column(Boolean, default=False)

    # AI config (override global)
    ai_provider = Column(String(50), default="")  # empty = use global
    ai_api_key_enc = Column(Text, default="")
    ai_base_url = Column(String(500), default="")
    ai_model = Column(String(100), default="")

    # Prompt template
    prompt_template = Column(Text, nullable=False)

    # Trading pairs JSON array, e.g. ["BTC-USDT-SWAP","ETH-USDT-SWAP"]
    symbols_json = Column(Text, nullable=False, default="[]")

    # Schedule
    interval_minutes = Column(Integer, default=15)

    # Risk parameters
    max_position_pct = Column(Float, default=10.0)  # % of equity per trade (legacy)
    position_usdt = Column(Float, default=100.0)    # Fixed USDT notional amount per trade
    leverage = Column(Integer, default=3)
    take_profit_pct = Column(Float, default=3.0)    # legacy, TP/SL now AI-determined
    stop_loss_pct = Column(Float, default=2.0)      # legacy

    # 涨幅榜自动扫描
    scan_enabled = Column(Boolean, default=False)         # 是否启用涨幅榜扫描
    scan_top_n = Column(Integer, default=5)               # 扫描前N个涨幅币
    scan_min_change_pct = Column(Float, default=3.0)      # 最低涨幅%才纳入
    scan_min_volume_usdt = Column(Float, default=500000)  # 最低24h成交额
    scan_exclude_symbols = Column(Text, default="[]")     # 排除的币种JSON
    use_ai = Column(Boolean, default=True)                # False = 纯规则网格模式

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    logs = relationship("StrategyLog", back_populates="strategy", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="strategy", cascade="all, delete-orphan")

    @property
    def symbols(self) -> list:
        try:
            return json.loads(self.symbols_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @symbols.setter
    def symbols(self, value: list):
        self.symbols_json = json.dumps(value)


class StrategyLog(Base):
    """Execution log for each strategy run"""
    __tablename__ = "strategy_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    symbol = Column(String(50), nullable=False)

    prompt_sent = Column(Text, default="")
    ai_raw_response = Column(Text, default="")
    parsed_action = Column(String(20), default="")  # long/short/hold/close_long/close_short
    parsed_confidence = Column(Integer, default=0)
    parsed_reasoning = Column(Text, default="")

    order_placed = Column(Boolean, default=False)
    order_id = Column(String(100), default="")
    error_message = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow)

    strategy = relationship("Strategy", back_populates="logs")


class Trade(Base):
    """Trade records"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    symbol = Column(String(50), nullable=False)

    side = Column(String(20), nullable=False)  # long / short
    action = Column(String(20), nullable=False)  # open / close
    price = Column(Float, nullable=False)
    quantity = Column(Float, nullable=False)
    order_id = Column(String(100), default="")

    # TP/SL
    tp_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)
    tp_order_id = Column(String(100), default="")
    sl_order_id = Column(String(100), default="")

    pnl = Column(Float, nullable=True)  # realized P&L for close trades
    fee = Column(Float, default=0.0)
    status = Column(String(20), default="filled")  # filled / cancelled / failed

    created_at = Column(DateTime, default=datetime.utcnow)

    strategy = relationship("Strategy", back_populates="trades")


class Position(Base):
    """Current positions (synced from OKX)"""
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    symbol = Column(String(50), nullable=False)
    side = Column(String(20), nullable=False)  # long / short
    size = Column(Float, default=0.0)
    avg_price = Column(Float, default=0.0)
    unrealized_pnl = Column(Float, default=0.0)
    leverage = Column(Integer, default=1)
    margin = Column(Float, default=0.0)

    tp_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EquitySnapshot(Base):
    """Periodic equity snapshots for chart"""
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    total_equity = Column(Float, nullable=False)
    available_balance = Column(Float, default=0.0)
    timestamp = Column(DateTime, default=datetime.utcnow)


class PromptTemplate(Base):
    """Reusable prompt templates"""
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(String(500), default="")
    content = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    # 简单迁移：为已有表添加新列
    _migrate_add_columns()
    db = SessionLocal()
    try:
        cfg = db.query(SystemConfig).first()
        if not cfg:
            db.add(SystemConfig(id=1))
            db.commit()
        # 种子：内置模板
        _seed_prompt_templates(db)
    finally:
        db.close()


def _seed_prompt_templates(db):
    """Seed built-in prompt templates; add any missing ones by name."""
    from app.routers.strategies import DEFAULT_PROMPT_TEMPLATE

    _SIMPLE_TEMPLATE = (
        "你是一位加密货币交易分析师。\n\n"
        "交易对: {symbol}\n当前价格: {current_price}\n\n"
        "最近K线数据:\n{klines}\n\n"
        "技术指标:\n- RSI(14): {rsi}\n"
        "- 布林带: 上轨={bb_upper}, 中轨={bb_middle}, 下轨={bb_lower}\n"
        "- MACD: {macd}, Signal: {macd_signal}, Histogram: {macd_histogram}\n"
        "- 成交量均值: {avg_volume}, 最新: {last_volume}\n\n"
        "持仓信息: {position_info}\n账户权益: {equity}\n\n"
        '请根据以上信息给出交易建议，只输出JSON：\n'
        '{{"action":"long/short/close_long/close_short/hold",'
        '"symbol":"{symbol}","confidence":0-100,"reasoning":"理由"}}'
    )

    _SCALPING_TEMPLATE = (
        "你是一位专注于高频合约网格剥头皮的交易机器人。\n\n"
        "交易对: {symbol}  当前价格: {current_price}\n\n"
        "=== 技术指标 ===\n"
        "RSI(14): {rsi}\n"
        "布林带: 上轨={bb_upper}  中轨={bb_middle}  下轨={bb_lower}\n"
        "布林带位置(0-100): {bb_position}  带宽(占价格%): {bb_width_pct:.2f}%\n"
        "MACD: {macd}  Signal: {macd_signal}  柱: {macd_histogram}\n"
        "成交量: 均值={avg_volume}  最新={last_volume}（放量比={volume_ratio:.2f}x）\n\n"
        "=== 最近5根K线(高,低,收,量) ===\n"
        "{klines}\n\n"
        "=== 持仓 ===\n"
        "{position_info}\n账户权益: {equity}\n\n"
        "=== 剥头皮决策规则（严格执行） ===\n"
        "1. 优先看布林带位置(bb_position)和带宽：\n"
        "   - bb_position ≥ 80 且 RSI ≥ 60：强超买，开空(short)，TP=中轨，SL=上轨+带宽*0.25\n"
        "   - bb_position ≤ 20 且 RSI ≤ 40：强超卖，开多(long)，TP=中轨，SL=下轨-带宽*0.25\n"
        "   - bb_position ≥ 70 且 RSI ≥ 55 且带宽<1%：中超买但带宽窄，谨慎开空，confidence≤55\n"
        "   - bb_position ≤ 30 且 RSI ≤ 45 且带宽<1%：中超卖但带宽窄，谨慎开多，confidence≤55\n"
        "2. 持仓管理：\n"
        "   - 持多仓：bb_position ≥ 60 → close_long（已到中轨上方止盈）\n"
        "   - 持空仓：bb_position ≤ 40 → close_short（已到中轨下方止盈）\n"
        "3. 带宽 < 0.5% 时市场极度盘整，所有方向输出 hold，confidence=0\n"
        "4. 成交量放量(volume_ratio ≥ 1.5)可适当提高confidence(+10)\n"
        "5. TP/SL 必须合理：多仓 tp > current_price > sl；空仓 tp < current_price < sl\n\n"
        "只输出纯JSON，不要markdown代码块，格式严格如下：\n"
        '{{"action":"long/short/close_long/close_short/hold",'
        '"symbol":"{symbol}",'
        '"confidence":0-100,'
        '"tp_price":数字或null,'
        '"sl_price":数字或null,'
        '"reasoning":"一句话理由"}}'
    )

    templates_data = [
        {
            "name": "增强版短线反转策略",
            "description": "包含布林带位置、成交量趋势、上影线分析的完整策略模板（推荐）",
            "content": DEFAULT_PROMPT_TEMPLATE,
            "is_default": True,
        },
        {
            "name": "基础分析模板",
            "description": "简洁的基础技术分析模板，适合快速测试",
            "content": _SIMPLE_TEMPLATE,
            "is_default": False,
        },
        {
            "name": "高频剥头皮·合约网格",
            "description": "短频快进快出，依据布林带位置自动剥头皮，TP=中轨，适合震荡行情",
            "content": _SCALPING_TEMPLATE,
            "is_default": False,
        },
    ]

    existing_names = {r.name for r in db.query(PromptTemplate.name).all()}
    added = False
    for td in templates_data:
        if td["name"] not in existing_names:
            # 新模板不抢占 is_default 标志
            if db.query(PromptTemplate).count() > 0:
                td = dict(td, is_default=False)
            db.add(PromptTemplate(**td))
            added = True
    if added:
        db.commit()


def _migrate_add_columns():
    """Add missing columns to existing tables (SQLite doesn't support ALTER TABLE ADD IF NOT EXISTS)."""
    import sqlite3
    conn = sqlite3.connect(DATABASE_URL.replace("sqlite:///", ""))
    cursor = conn.cursor()
    # system_config 迁移
    cursor.execute("PRAGMA table_info(system_config)")
    existing = {row[1] for row in cursor.fetchall()}
    migrations = [
        ("okx_proxy", "TEXT DEFAULT ''"),
        ("okx_sim_api_key_enc", "TEXT DEFAULT ''"),
        ("okx_sim_secret_key_enc", "TEXT DEFAULT ''"),
        ("okx_sim_passphrase_enc", "TEXT DEFAULT ''"),
        ("tg_bot_token_enc", "TEXT DEFAULT ''"),
        ("tg_chat_id", "VARCHAR(200) DEFAULT ''"),
        ("tg_proxy", "VARCHAR(500) DEFAULT ''"),
    ]
    for col_name, col_type in migrations:
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE system_config ADD COLUMN {col_name} {col_type}")

    # strategies 迁移
    cursor.execute("PRAGMA table_info(strategies)")
    existing_strat = {row[1] for row in cursor.fetchall()}
    strat_migrations = [
        ("scan_enabled", "INTEGER DEFAULT 0"),
        ("scan_top_n", "INTEGER DEFAULT 5"),
        ("scan_min_change_pct", "REAL DEFAULT 3.0"),
        ("scan_min_volume_usdt", "REAL DEFAULT 500000"),
        ("scan_exclude_symbols", "TEXT DEFAULT '[]'"),
        ("position_usdt", "REAL DEFAULT 100.0"),
        ("use_ai", "INTEGER DEFAULT 1"),
    ]
    for col_name, col_type in strat_migrations:
        if col_name not in existing_strat:
            cursor.execute(f"ALTER TABLE strategies ADD COLUMN {col_name} {col_type}")

    conn.commit()
    conn.close()
