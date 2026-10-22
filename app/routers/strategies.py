"""Strategy CRUD API routes"""

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session

from app.models import Strategy, get_db
from app.schemas import StrategyCreate, StrategyUpdate, StrategyOut
from app.config import encrypt_value
from app.engine import update_strategy_job, run_strategy
from app.exchange import fetch_top_gainers

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _strategy_to_out(s: Strategy) -> StrategyOut:
    """Helper to build StrategyOut from a Strategy model."""
    try:
        exclude_syms = json.loads(s.scan_exclude_symbols or "[]")
    except (json.JSONDecodeError, TypeError):
        exclude_syms = []
    return StrategyOut(
        id=s.id, name=s.name, enabled=s.enabled,
        ai_provider=s.ai_provider or "", ai_base_url=s.ai_base_url or "",
        ai_model=s.ai_model or "",
        prompt_template=s.prompt_template,
        symbols=s.symbols,
        interval_minutes=s.interval_minutes,
        position_usdt=s.position_usdt or 100.0,
        max_position_pct=s.max_position_pct,
        leverage=s.leverage,
        take_profit_pct=s.take_profit_pct,
        stop_loss_pct=s.stop_loss_pct,
        scan_enabled=s.scan_enabled or False,
        scan_top_n=s.scan_top_n or 5,
        scan_min_change_pct=s.scan_min_change_pct or 3.0,
        scan_min_volume_usdt=s.scan_min_volume_usdt or 500000,
        scan_exclude_symbols=exclude_syms,
        use_ai=s.use_ai if s.use_ai is not None else True,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


DEFAULT_PROMPT_TEMPLATE = """你是一位经验丰富的加密货币短线交易员，擅长捕捉超买/超卖后的反转机会。

【当前时间】：{current_time}
【交易对】：{symbol}
【当前价格】：{current_price}
【本策略开仓名义金额（USDT）】：{position_notional_usdt}
【账户总权益（USDT）】：{equity}
【当前该币种持仓】：{position_info}（如果无持仓则显示"无"）

【最近15根15分钟K线（时间,开,高,低,收,成交量）】：
{klines}

【技术指标（基于上述K线计算）】：
- RSI(14): {rsi}
- 布林带(20,2): 上轨={bb_upper}, 中轨={bb_middle}, 下轨={bb_lower}
- 当前价格位于布林带的位置：{bb_position}%（0=下轨, 100=上轨）
- 最近3根K线的成交量变化趋势：{volume_trend}（放量/缩量/持平）
- 最近一根K线的上影线/实体比例：{upper_shadow_ratio}（若>2表示长上影）

【你的任务】：
根据以上数据，判断是否应该开仓或平仓。优先考虑做空机会（因为震荡市中超买回落更常见），只有在明显超卖且出现底部形态时才做多。

【决策规则参考】：
1. **做空条件**（满足任意2条即可）：
   - RSI > 70 且当前价格高于布林带上轨的95%位置
   - 最近一根K线出现长上影线（上影线/实体 > 2）且成交量萎缩
   - 连续三根K线高点逐步降低，且RSI从超买区拐头向下
2. **做多条件**（需同时满足）：
   - RSI < 30 且价格低于布林带下轨
   - 最近一根K线为阳线且收盘价接近最高价（下影线短）
3. **平仓条件**：
   - 若持有多单，当RSI > 65 或价格触及布林带上轨 → 平多
   - 若持有空单，当RSI < 35 或价格触及布林带下轨 → 平空
4. **无操作**：其它情况都输出 hold。

【风险控制】：
- 如果已经有反向持仓（例如已有空单却收到做多信号），优先平掉反向仓位，不开新仓。

【止盈止损设置】：
- 当 action 为 "long" 或 "short" 时，你必须同时给出止盈价（tp_price）和止损价（sl_price）。
- 止盈止损应基于当前价格、技术指标支撑阻力位、波动率等综合判断，给出具体价格数值，不要给百分比。
- **对于 long（做多）**：TP必须严格大于现价（TP > {current_price}），SL必须严格小于现价（SL < {current_price}），TP和SL必须不相等。
- **对于 short（做空）**：TP必须严格小于现价（TP < {current_price}），SL必须严格大于现价（SL > {current_price}），TP和SL必须不相等。
- 当 action 为平仓或 hold 时，tp_price 和 sl_price 填 null。

【输出格式要求】：
你必须只输出一个合法的 JSON 对象，不要包含任何其他文字、注释或解释。格式如下：
{{
  "action": "short" 或 "long" 或 "close_short" 或 "close_long" 或 "hold",
  "symbol": "{symbol}",
  "confidence": 0-100的整数,
  "reasoning": "简短的中文理由（不超过30字）",
  "tp_price": 止盈价格数值（开仓时必填，平仓/hold时填null；long时必须>现价，short时必须<现价）,
  "sl_price": 止损价格数值（开仓时必填，平仓/hold时填null；long时必须<现价，short时必须>现价）
}}

现在请基于以上数据做出决策。"""


@router.get("", response_model=list[StrategyOut])
def list_strategies(db: Session = Depends(get_db)):
    strategies = db.query(Strategy).order_by(Strategy.id.desc()).all()
    result = []
    for s in strategies:
        result.append(_strategy_to_out(s))
    return result


@router.get("/default-prompt")
def get_default_prompt():
    return {"prompt_template": DEFAULT_PROMPT_TEMPLATE}


class ScanGainersRequest(BaseModel):
    top_n: int = Field(5, ge=1, le=50)
    min_change_pct: float = Field(3.0, ge=0.5)
    min_volume_usdt: float = Field(500000, ge=0)
    exclude_symbols: list[str] = []


@router.post("/scan-gainers")
def scan_gainers(req: ScanGainersRequest):
    """测试扫描涨幅榜，返回当前涨幅榜数据。"""
    default_exclude = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    all_exclude = list(set(req.exclude_symbols + default_exclude))
    try:
        results = fetch_top_gainers(
            top_n=req.top_n,
            min_volume_usdt=req.min_volume_usdt,
            exclude_symbols=all_exclude,
        )
        if results is None:
            raise HTTPException(502, "扫描失败: 无法从 OKX 获取涨幅榜数据")
        filtered = [r for r in results if r["change_pct_1h"] >= req.min_change_pct]
        return filtered
    except Exception as e:
        raise HTTPException(500, f"扫描失败: {str(e)}")


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(strategy_id: int, db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        raise HTTPException(404, "策略不存在")
    return _strategy_to_out(s)


@router.post("", response_model=StrategyOut)
def create_strategy(data: StrategyCreate, db: Session = Depends(get_db)):
    s = Strategy(
        name=data.name,
        enabled=data.enabled,
        ai_provider=data.ai_provider,
        ai_api_key_enc=encrypt_value(data.ai_api_key) if data.ai_api_key else "",
        ai_base_url=data.ai_base_url,
        ai_model=data.ai_model,
        prompt_template=data.prompt_template,
        symbols_json=json.dumps(data.symbols),
        interval_minutes=data.interval_minutes,
        position_usdt=data.position_usdt,
        max_position_pct=data.max_position_pct,
        leverage=data.leverage,
        take_profit_pct=data.take_profit_pct,
        stop_loss_pct=data.stop_loss_pct,
        scan_enabled=data.scan_enabled,
        scan_top_n=data.scan_top_n,
        scan_min_change_pct=data.scan_min_change_pct,
        scan_min_volume_usdt=data.scan_min_volume_usdt,
        scan_exclude_symbols=json.dumps(data.scan_exclude_symbols),
        use_ai=data.use_ai if data.use_ai is not None else True,
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    if s.enabled:
        update_strategy_job(s.id)

    return _strategy_to_out(s)


@router.put("/{strategy_id}", response_model=StrategyOut)
def update_strategy(strategy_id: int, data: StrategyUpdate, db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        raise HTTPException(404, "策略不存在")

    if data.name is not None:
        s.name = data.name
    if data.enabled is not None:
        s.enabled = data.enabled
    if data.ai_provider is not None:
        s.ai_provider = data.ai_provider
    if data.ai_api_key is not None and data.ai_api_key:
        s.ai_api_key_enc = encrypt_value(data.ai_api_key)
    if data.ai_base_url is not None:
        s.ai_base_url = data.ai_base_url
    if data.ai_model is not None:
        s.ai_model = data.ai_model
    if data.prompt_template is not None:
        s.prompt_template = data.prompt_template
    if data.symbols is not None:
        s.symbols_json = json.dumps(data.symbols)
    if data.interval_minutes is not None:
        s.interval_minutes = data.interval_minutes
    if data.position_usdt is not None:
        s.position_usdt = data.position_usdt
    if data.max_position_pct is not None:
        s.max_position_pct = data.max_position_pct
    if data.leverage is not None:
        s.leverage = data.leverage
    if data.take_profit_pct is not None:
        s.take_profit_pct = data.take_profit_pct
    if data.stop_loss_pct is not None:
        s.stop_loss_pct = data.stop_loss_pct
    if data.scan_enabled is not None:
        s.scan_enabled = data.scan_enabled
    if data.scan_top_n is not None:
        s.scan_top_n = data.scan_top_n
    if data.scan_min_change_pct is not None:
        s.scan_min_change_pct = data.scan_min_change_pct
    if data.scan_min_volume_usdt is not None:
        s.scan_min_volume_usdt = data.scan_min_volume_usdt
    if data.scan_exclude_symbols is not None:
        s.scan_exclude_symbols = json.dumps(data.scan_exclude_symbols)
    if data.use_ai is not None:
        s.use_ai = data.use_ai

    db.commit()
    db.refresh(s)
    update_strategy_job(s.id)

    return _strategy_to_out(s)


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        raise HTTPException(404, "策略不存在")
    db.delete(s)
    db.commit()
    update_strategy_job(strategy_id)  # Remove job after delete
    return {"message": "已删除"}


@router.post("/{strategy_id}/run")
def run_strategy_now(strategy_id: int, db: Session = Depends(get_db)):
    """Manually run a strategy immediately."""
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        raise HTTPException(404, "策略不存在")
    try:
        run_strategy(strategy_id)
        return {"message": f"策略 '{s.name}' 已执行完成"}
    except Exception as e:
        raise HTTPException(500, f"策略执行失败: {str(e)}")


@router.post("/{strategy_id}/toggle")
def toggle_strategy(strategy_id: int, db: Session = Depends(get_db)):
    """Toggle strategy enabled/disabled."""
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        raise HTTPException(404, "策略不存在")
    s.enabled = not s.enabled
    db.commit()
    update_strategy_job(s.id)
    return {"enabled": s.enabled}
