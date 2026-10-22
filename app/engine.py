"""Strategy execution engine and scheduler"""

import json
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.models import (
    SessionLocal, Strategy, StrategyLog, Trade, Position, EquitySnapshot
)
from app.exchange import (
    fetch_ohlcv, fetch_ticker, fetch_balance, fetch_positions,
    place_order, close_position, place_tp_sl, fetch_top_gainers, get_exchange,
)
from app.indicators import calc_indicators, format_klines
from app.ai_client import call_ai

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(daemon=True)


AI_TPSL_APPENDIX = """

【硬性输出要求（系统附加）】：
- 当 action 为 \"long\" 或 \"short\" 时，必须返回 tp_price 和 sl_price，且为数字。
- 当 action 为 \"close_long\"、\"close_short\" 或 \"hold\" 时，tp_price 和 sl_price 返回 null。
- 仅输出 JSON，不要输出其他文本。
"""


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _fee_cost(order) -> float:
    fee = _as_dict(_as_dict(order).get("fee"))
    return _to_float(fee.get("cost"), 0.0)


def _with_tpsl_instruction(prompt: str) -> str:
    """Append strict TP/SL output requirement for all strategies."""
    base = prompt or ""
    return f"{base.rstrip()}\n{AI_TPSL_APPENDIX}" if base.strip() else AI_TPSL_APPENDIX.strip()


def _extract_usdt_balance(balance: dict) -> tuple[float, float]:
    """Extract USDT total/free from different ccxt/OKX balance structures."""
    if not isinstance(balance, dict):
        return 0.0, 0.0

    total_candidates = [
        balance.get("total", {}).get("USDT"),
        balance.get("USDT", {}).get("total"),
    ]
    free_candidates = [
        balance.get("free", {}).get("USDT"),
        balance.get("USDT", {}).get("free"),
    ]

    info = balance.get("info", {})
    if isinstance(info, dict):
        data = info.get("data", [])
        if isinstance(data, list) and data:
            d0 = data[0] if isinstance(data[0], dict) else {}
            # Unified account may only expose totalEq/adjEq at account level.
            total_candidates.extend([d0.get("totalEq"), d0.get("adjEq")])
            free_candidates.extend([d0.get("adjEq")])

            details = d0.get("details", [])
            if isinstance(details, list):
                # Prefer USDT detail if present.
                for d in details:
                    if not isinstance(d, dict):
                        continue
                    if d.get("ccy") == "USDT":
                        total_candidates.extend([d.get("eq"), d.get("eqUsd"), d.get("cashBal")])
                        free_candidates.extend([d.get("availEq"), d.get("availBal")])
                        break

                # Fallback: if no USDT detail, aggregate eqUsd from all currencies.
                if not any(_to_float(x) > 0 for x in total_candidates):
                    usd_sum = 0.0
                    for d in details:
                        if isinstance(d, dict):
                            usd_sum += _to_float(d.get("eqUsd"), 0.0)
                    if usd_sum > 0:
                        total_candidates.append(usd_sum)

    equity = 0.0
    for item in total_candidates:
        v = _to_float(item)
        if v > 0:
            equity = v
            break

    available = 0.0
    for item in free_candidates:
        v = _to_float(item)
        if v > 0:
            available = v
            break

    return equity, available


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    if not s:
        return ""
    # ccxt position symbol may be like BTC/USDT:USDT, normalize to BTC-USDT-SWAP
    if "/" in s and ":" in s:
        base_quote = s.split(":", 1)[0].replace("/", "-")
        return f"{base_quote}-SWAP"
    return s


def _normalize_side(side: str) -> str:
    s = (side or "").lower().strip()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s


def _get_market_specs(symbol: str) -> tuple[float, object, float]:
    """Return (min_amount, precision, contract_size) for a symbol.

    For OKX SWAP, `amount` is contract size (number of contracts), not base coin amount.
    """
    exchange = get_exchange()
    min_amount = 0.0
    precision = None
    contract_size = 1.0

    try:
        market = exchange.market(symbol)
        if isinstance(market, dict):
            min_amount = _to_float((market.get("limits", {}).get("amount", {}) or {}).get("min"), 0.0)
            precision = (market.get("precision", {}) or {}).get("amount")
            contract_size = _to_float(
                market.get("contractSize")
                or (market.get("info", {}) or {}).get("ctVal"),
                1.0,
            )
    except Exception:
        # Fallback defaults when market metadata is unavailable.
        pass

    if contract_size <= 0:
        contract_size = 1.0

    return min_amount, precision, contract_size


def _get_price_tick_size(symbol: str) -> float:
    """Return the minimum price increment for a symbol."""
    exchange = get_exchange()
    tick_size = 0.0001

    try:
        market = exchange.market(symbol)
        if isinstance(market, dict):
            raw_precision = (market.get("precision", {}) or {}).get("price")
            if isinstance(raw_precision, int):
                tick_size = 10 ** (-raw_precision) if raw_precision >= 0 else tick_size
            else:
                precision_value = _to_float(raw_precision, 0.0)
                if precision_value > 0:
                    tick_size = precision_value if precision_value < 1 else 10 ** (-int(precision_value))
    except Exception:
        pass

    return tick_size if tick_size > 0 else 0.0001


def _adjust_tpsl_prices(symbol: str, side: str, current_price: float, tp_price: float, sl_price: float) -> tuple[float, float] | None:
    """Validate and nudge borderline TP/SL values. Returns None if direction is fundamentally wrong."""
    tick_size = _get_price_tick_size(symbol)
    
    # For short: TP should be < current_price, SL should be > current_price.
    # For long: TP should be > current_price, SL should be < current_price.
    # Only nudge if within ~0.5% of boundary (a few ticks worth), reject if direction is fundamentally wrong.
    boundary_tolerance = current_price * 0.005  # 0.5%
    
    if side == "long":
        # TP must be > current_price for long (profit when price goes up)
        if tp_price < current_price - boundary_tolerance:
            return None  # Fundamentally wrong direction
        if tp_price <= current_price:
            tp_price = current_price + tick_size  # Nudge up by one tick
        
        # SL must be < current_price for long (loss when price goes down)
        if sl_price > current_price + boundary_tolerance:
            return None  # Fundamentally wrong direction
        if sl_price >= current_price:
            sl_price = current_price - tick_size  # Nudge down by one tick
            
    elif side == "short":
        # TP must be < current_price for short (profit when price goes down)
        if tp_price > current_price + boundary_tolerance:
            return None  # Fundamentally wrong direction
        if tp_price >= current_price:
            tp_price = current_price - tick_size  # Nudge down by one tick
        
        # SL must be > current_price for short (loss when price goes up)
        if sl_price < current_price - boundary_tolerance:
            return None  # Fundamentally wrong direction
        if sl_price <= current_price:
            sl_price = current_price + tick_size  # Nudge up by one tick

    try:
        exchange = get_exchange()
        tp_price = float(exchange.price_to_precision(symbol, tp_price))
        sl_price = float(exchange.price_to_precision(symbol, sl_price))
    except Exception:
        tp_price = round(tp_price, 8)
        sl_price = round(sl_price, 8)

    return tp_price, sl_price


def _normalize_order_amount(symbol: str, amount: float) -> tuple[float, float, object]:
    """Normalize contract amount by exchange precision and return (normalized, min_amount, precision)."""
    exchange = get_exchange()
    min_amount, precision, _ = _get_market_specs(symbol)

    normalized = amount
    try:
        normalized = float(exchange.amount_to_precision(symbol, amount))
    except Exception:
        # Fallback rounding when precision conversion is unavailable.
        normalized = round(amount, 8)

    return normalized, min_amount, precision


def start_scheduler():
    """Initialize and start the scheduler, loading all enabled strategies."""
    if scheduler.running:
        return
    scheduler.start()
    reload_all_jobs()
    logger.info("Scheduler started")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def reload_all_jobs():
    """Reload jobs for all strategies from DB."""
    # Remove existing strategy jobs
    for job in scheduler.get_jobs():
        if job.id.startswith("strategy_"):
            job.remove()

    db = SessionLocal()
    try:
        strategies = db.query(Strategy).filter(Strategy.enabled == True).all()
        for s in strategies:
            _add_strategy_job(s)
        logger.info(f"Loaded {len(strategies)} strategy jobs")
    finally:
        db.close()

    # Add position sync job (every 2 minutes)
    if not scheduler.get_job("sync_positions"):
        scheduler.add_job(
            sync_positions,
            IntervalTrigger(minutes=2),
            id="sync_positions",
            replace_existing=True,
            max_instances=1,
        )

    # Add equity snapshot job (every 5 minutes)
    if not scheduler.get_job("equity_snapshot"):
        scheduler.add_job(
            record_equity_snapshot,
            IntervalTrigger(minutes=5),
            id="equity_snapshot",
            replace_existing=True,
            max_instances=1,
        )


def _add_strategy_job(strategy: Strategy):
    """Add a scheduled job for a strategy."""
    job_id = f"strategy_{strategy.id}"
    scheduler.add_job(
        run_strategy,
        IntervalTrigger(minutes=strategy.interval_minutes),
        id=job_id,
        args=[strategy.id],
        replace_existing=True,
        max_instances=1,
    )
    logger.info(f"Added job {job_id} every {strategy.interval_minutes}min")


def update_strategy_job(strategy_id: int):
    """Update or remove a strategy's scheduled job."""
    db = SessionLocal()
    try:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        job_id = f"strategy_{strategy_id}"

        if not strategy or not strategy.enabled:
            existing = scheduler.get_job(job_id)
            if existing:
                existing.remove()
                logger.info(f"Removed job {job_id}")
            return

        _add_strategy_job(strategy)
    finally:
        db.close()


def run_strategy(strategy_id: int):
    """Execute a strategy: collect data, call AI, execute trades."""
    db = SessionLocal()
    try:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            logger.error(f"Strategy {strategy_id} not found")
            return

        # 确定交易对列表：扫描模式 vs 固定模式
        if strategy.scan_enabled:
            symbols = _scan_symbols_for_strategy(strategy)
            if symbols is None:
                logger.warning(f"Strategy {strategy_id}: 涨幅榜扫描失败，已跳过本轮")
                return
            if not symbols:
                logger.info(f"Strategy {strategy_id}: 涨幅榜扫描未找到符合条件的币种")
                return
            logger.info(f"Strategy {strategy_id}: 涨幅榜扫描到 {len(symbols)} 个币种: {symbols}")
        else:
            symbols = strategy.symbols

        if not symbols:
            logger.warning(f"Strategy {strategy_id} has no symbols configured")
            return

        # Get account info
        balance = fetch_balance()
        if not balance:
            for symbol in symbols:
                log = StrategyLog(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    error_message="读取账户权益失败：请检查 API 凭证、代理和实盘/模拟盘开关",
                )
                db.add(log)
            db.commit()
            logger.error(f"Strategy {strategy_id}: balance fetch returned empty")
            return

        equity, available = _extract_usdt_balance(balance)
        if equity <= 0:
            dbg_keys = list(balance.keys())
            for symbol in symbols:
                log = StrategyLog(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    error_message=f"读取到权益为0，请检查账户资金是否在合约账户。balance keys={dbg_keys}",
                )
                db.add(log)
            db.commit()
            logger.error(f"Strategy {strategy_id}: extracted equity=0 from balance keys={dbg_keys}")
            return

        # Get current positions from exchange
        all_positions = fetch_positions()

        for symbol in symbols:
            try:
                _run_strategy_for_symbol(db, strategy, symbol, equity, available, all_positions)
            except Exception as e:
                logger.error(f"Strategy {strategy_id} error on {symbol}: {e}", exc_info=True)
                log = StrategyLog(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    error_message=str(e),
                )
                db.add(log)
                db.commit()

    except Exception as e:
        logger.error(f"Strategy {strategy_id} execution failed: {e}", exc_info=True)
    finally:
        db.close()


def _scan_symbols_for_strategy(strategy: Strategy) -> list[str]:
    """扫描涨幅榜，根据策略配置返回符合条件的交易对列表。"""
    import json as _json
    try:
        exclude = _json.loads(strategy.scan_exclude_symbols or "[]")
    except (ValueError, TypeError):
        exclude = []

    # 固定排除主流币（BTC/ETH 通常不算山寨币）
    default_exclude = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    all_exclude = list(set(exclude + default_exclude))

    gainers = fetch_top_gainers(
        top_n=strategy.scan_top_n or 5,
        min_volume_usdt=strategy.scan_min_volume_usdt or 500000,
        exclude_symbols=all_exclude,
    )
    if gainers is None:
        return None

    # 额外过滤：只保留涨幅 >= 最低涨幅的
    min_change = strategy.scan_min_change_pct or 3.0
    filtered = [g for g in gainers if g["change_pct_1h"] >= min_change]

    # 也把手动配置的 symbols 加进来（不重复）
    manual_symbols = strategy.symbols or []
    result_symbols = [g["symbol"] for g in filtered]
    for s in manual_symbols:
        if s not in result_symbols:
            result_symbols.append(s)

    return result_symbols


def _rules_engine(symbol: str, indicators: dict, all_positions: list) -> dict:
    """
    Pure rule-based grid-like strategy. No AI required.

    Logic (Bollinger Band grid):
    - BB position 0–100 maps the price between lower and upper band.
    - Zones:
        * bb_pos <= 20  → oversold zone → look to LONG (or close short)
        * bb_pos >= 80  → overbought zone → look to SHORT (or close long)
        * 30 < bb_pos < 70 → neutral → hold
    - RSI confirmation: RSI < 40 for long entries, RSI > 60 for short entries.
    - TP/SL derived from Bollinger Bands:
        Long : TP = bb_middle, SL = bb_lower - (bb_middle - bb_lower) * 0.3
        Short: TP = bb_middle, SL = bb_upper + (bb_upper - bb_middle) * 0.3
    - If already holding a position:
        * Long and bb_pos >= 65 → close_long
        * Short and bb_pos <= 35 → close_short
    """
    bb_pos    = _to_float(indicators.get("bb_position"), 50.0)
    rsi       = _to_float(indicators.get("rsi"), 50.0)
    bb_upper  = _to_float(indicators.get("bb_upper"), 0.0)
    bb_middle = _to_float(indicators.get("bb_middle"), 0.0)
    bb_lower  = _to_float(indicators.get("bb_lower"), 0.0)
    price     = _to_float(indicators.get("current_price"), 0.0)

    # Check existing position for this symbol
    cur_side = None
    for p in all_positions:
        if p.get("symbol") == symbol and abs(_to_float(p.get("contracts"), 0)) > 0:
            cur_side = _normalize_side(p.get("side", ""))
            break

    # Close signals take priority
    if cur_side == "long" and bb_pos >= 65:
        return {"action": "close_long", "confidence": 70,
                "reasoning": f"布林带位置 {bb_pos:.1f} 回到中轨上方，多单止盈平仓",
                "tp_price": None, "sl_price": None}
    if cur_side == "short" and bb_pos <= 35:
        return {"action": "close_short", "confidence": 70,
                "reasoning": f"布林带位置 {bb_pos:.1f} 回到中轨下方，空单止盈平仓",
                "tp_price": None, "sl_price": None}

    # Don't open a new position if already holding one on this symbol
    if cur_side is not None:
        return {"action": "hold", "confidence": 60,
                "reasoning": f"已有 {cur_side} 仓位，等待平仓信号",
                "tp_price": None, "sl_price": None}

    # Band must be valid before computing TP/SL
    if bb_upper <= 0 or bb_lower <= 0 or bb_middle <= 0 or price <= 0:
        return {"action": "hold", "confidence": 50,
                "reasoning": "布林带数据不足，跳过本轮",
                "tp_price": None, "sl_price": None}

    band_width = bb_upper - bb_lower

    # Short entry: overbought zone
    if bb_pos >= 80 and rsi >= 60:
        tp = round(bb_middle, 4)
        sl = round(bb_upper + band_width * 0.3, 4)
        return {"action": "short", "confidence": 75,
                "reasoning": f"布林带位置 {bb_pos:.1f} ≥ 80，RSI={rsi:.1f} 超买，网格做空，TP={tp} SL={sl}",
                "tp_price": tp, "sl_price": sl}

    # Long entry: oversold zone
    if bb_pos <= 20 and rsi <= 40:
        tp = round(bb_middle, 4)
        sl = round(bb_lower - band_width * 0.3, 4)
        return {"action": "long", "confidence": 75,
                "reasoning": f"布林带位置 {bb_pos:.1f} ≤ 20，RSI={rsi:.1f} 超卖，网格做多，TP={tp} SL={sl}",
                "tp_price": tp, "sl_price": sl}

    return {"action": "hold", "confidence": 55,
            "reasoning": f"布林带位置 {bb_pos:.1f}，RSI={rsi:.1f}，无明确信号",
            "tp_price": None, "sl_price": None}


def _run_strategy_for_symbol(db, strategy: Strategy, symbol: str,
                              equity: float, available: float, all_positions: list):
    """Run strategy logic for a single symbol."""
    # 1. Fetch market data
    candles = fetch_ohlcv(symbol, "15m", 20)
    ticker = fetch_ticker(symbol)
    indicators = calc_indicators(candles)
    klines_str = format_klines(candles)
    current_price = indicators.get("current_price", ticker.get("last", 0))

    # 2. Get position info for this symbol
    pos_info = "无持仓"
    for p in all_positions:
        if p.get("symbol") == symbol:
            side = p.get("side", "")
            contracts = p.get("contracts", 0)
            entry_price = p.get("entryPrice", 0)
            unrealized_pnl = p.get("unrealizedPnl", 0)
            pos_info = f"方向: {side}, 数量: {contracts}, 均价: {entry_price}, 未实现盈亏: {unrealized_pnl}"
            break

    # 3. Build prompt from template
    from datetime import datetime as _dt
    template_vars = {
        "symbol": symbol,
        "klines": klines_str,
        "current_time": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "current_price": str(current_price),
        "bb_upper": str(indicators.get("bb_upper", "N/A")),
        "bb_middle": str(indicators.get("bb_middle", "N/A")),
        "bb_lower": str(indicators.get("bb_lower", "N/A")),
        "bb_position": str(indicators.get("bb_position", "N/A")),
        "rsi": str(indicators.get("rsi", "N/A")),
        "macd": str(indicators.get("macd", "N/A")),
        "macd_signal": str(indicators.get("macd_signal", "N/A")),
        "macd_histogram": str(indicators.get("macd_histogram", "N/A")),
        "avg_volume": str(indicators.get("avg_volume", "N/A")),
        "last_volume": str(indicators.get("last_volume", "N/A")),
        "volume_change": str(indicators.get("volume_change", "N/A")),
        "volume_trend": str(indicators.get("volume_trend", "N/A")),
        "upper_shadow_ratio": str(indicators.get("upper_shadow_body_ratio", "N/A")),
        "equity": str(round(equity, 2)),
        "available_balance": str(round(available, 2)),
        "position_info": pos_info,
        "indicators": json.dumps(indicators, ensure_ascii=False),
        # 衍生指标：带宽百分比 & 成交量放量倍数（供剥头皮模板使用）
        "bb_width_pct": _to_float(
            (indicators.get("bb_upper", 0) - indicators.get("bb_lower", 0))
            / indicators.get("bb_middle", 1) * 100
            if indicators.get("bb_middle") else 0,
            0.0,
        ),
        "volume_ratio": _to_float(
            indicators.get("last_volume", 0) / indicators.get("avg_volume", 1)
            if indicators.get("avg_volume") else 1.0,
            1.0,
        ),
        "account_balance": str(round(equity, 2)),
        "position_notional_usdt": str(round(_to_float(getattr(strategy, "position_usdt", 0.0), 0.0), 2)),
    }

    try:
        prompt = strategy.prompt_template.format(**template_vars)
    except KeyError as e:
        prompt = strategy.prompt_template
        for k, v in template_vars.items():
            prompt = prompt.replace("{" + k + "}", v)

    prompt = _with_tpsl_instruction(prompt)

    # 4. Call AI or use rules engine
    use_ai = getattr(strategy, "use_ai", True)
    if use_ai is None:
        use_ai = True

    if not use_ai:
        ai_result = _rules_engine(symbol, indicators, all_positions)
        prompt = ""  # 纯规则模式不发送 Prompt
    else:
        ai_result = call_ai(strategy, prompt)

    # 5. Create log entry
    log = StrategyLog(
        strategy_id=strategy.id,
        symbol=symbol,
        prompt_sent=prompt,
    )

    if ai_result is None:
        log.error_message = "AI返回格式错误或超时"
        db.add(log)
        db.commit()
        return

    log.ai_raw_response = json.dumps(ai_result, ensure_ascii=False)
    log.parsed_action = ai_result["action"]
    log.parsed_confidence = ai_result["confidence"]
    log.parsed_reasoning = ai_result["reasoning"]

    # 6. Execute trade based on AI decision
    action = ai_result["action"]

    if action == "hold":
        db.add(log)
        db.commit()
        return

    try:
        if action in ("long", "short"):
            ai_tp = ai_result.get("tp_price")
            ai_sl = ai_result.get("sl_price")
            _execute_open(db, strategy, symbol, action, current_price, equity, all_positions, log,
                          ai_tp_price=ai_tp, ai_sl_price=ai_sl)
        elif action in ("close_long", "close_short"):
            close_side = "long" if action == "close_long" else "short"
            _execute_close(db, strategy, symbol, close_side, all_positions, log)
    except Exception as e:
        log.error_message = f"交易执行失败: {str(e)}"
        logger.error(f"Trade execution error: {e}", exc_info=True)

    db.add(log)
    db.commit()


def _execute_open(db, strategy: Strategy, symbol: str, side: str,
                   current_price: float, equity: float, all_positions: list, log: StrategyLog,
                   ai_tp_price=None, ai_sl_price=None):
    """Execute an open position order."""
    if current_price <= 0 or equity <= 0:
        log.error_message = f"价格或权益数据无效: price={current_price}, equity={equity}"
        return

    # Check if there's an opposite position - close it first
    for p in all_positions:
        if p.get("symbol") == symbol:
            p_side = p.get("side", "")
            p_contracts = float(p.get("contracts", 0))
            if p_contracts > 0 and p_side != side:
                logger.info(f"Closing opposite position: {symbol} {p_side} {p_contracts}")
                close_order = close_position(symbol, p_side, p_contracts)
                close_order_dict = _as_dict(close_order)
                trade = Trade(
                    strategy_id=strategy.id,
                    symbol=symbol,
                    side=p_side,
                    action="close",
                    price=current_price,
                    quantity=p_contracts,
                    order_id=close_order_dict.get("id", ""),
                    pnl=_to_float(_as_dict(close_order_dict.get("info")).get("pnl"), 0.0),
                    fee=_fee_cost(close_order_dict),
                )
                db.add(trade)

    # Check max position limit.
    # position_usdt is fixed USDT NOTIONAL per trade in the new mode.
    position_usdt = _to_float(getattr(strategy, 'position_usdt', None) or 0)
    if position_usdt <= 0:
        # Legacy fallback: old max_position_pct is margin-based, convert to notional with leverage.
        position_usdt = equity * (strategy.max_position_pct / 100) * max(strategy.leverage, 1)

    _, _, contract_size = _get_market_specs(symbol)

    existing_pos_notional = 0.0
    for p in all_positions:
        if p.get("symbol") == symbol and p.get("side") == side:
            existing_pos_notional = float(p.get("contracts", 0)) * current_price * contract_size
            break

    remaining_notional = position_usdt - existing_pos_notional
    if remaining_notional <= 0:
        log.error_message = f"已达到最大名义仓位限制 ({position_usdt:.2f} USDT)"
        return

    # Calculate quantity directly from notional amount.
    trade_notional = min(remaining_notional, position_usdt)
    trade_margin = trade_notional / max(strategy.leverage, 1)
    denom = current_price * contract_size
    if denom <= 0:
        log.error_message = f"合约参数无效: price={current_price}, contract_size={contract_size}"
        return

    # Convert quote notional (USDT) to contract size for OKX swap.
    raw_quantity = trade_notional / denom
    quantity, min_amount, precision = _normalize_order_amount(symbol, raw_quantity)

    if quantity <= 0:
        log.error_message = "计算开仓数量为0"
        return

    if min_amount > 0 and quantity < min_amount:
        min_notional = min_amount * current_price * contract_size
        log.error_message = (
            f"下单数量过小: 名义金额={trade_notional:.2f}U, 预估保证金={trade_margin:.2f}U, "
            f"计算张数={quantity}, 最小张数={min_amount}, 合约面值={contract_size}, "
            f"最小名义约={min_notional:.2f}U, 精度={precision}; "
            f"请提高开仓名义金额或更换价格更低/面值更小的交易对"
        )
        return

    # TP/SL must come from AI for open orders - VALIDATE BEFORE PLACING ORDER.
    try:
        tp_price = round(float(ai_tp_price), 4)
        sl_price = round(float(ai_sl_price), 4)
    except (TypeError, ValueError):
        log.error_message = "AI未返回有效止盈止损价格，本次不开仓"
        return

    adjusted = _adjust_tpsl_prices(symbol, side, current_price, tp_price, sl_price)
    if adjusted is None:
        log.error_message = f"AI止盈止损方向错误: {side} 现价={current_price}, AI TP={ai_tp_price}, SL={ai_sl_price}，超出容差范围"
        return
    tp_price, sl_price = adjusted

    # Basic directional validation to avoid wrong-side TP/SL.
    if side == "long" and not (tp_price > current_price and sl_price < current_price):
        log.error_message = f"AI止盈止损方向无效: long 需 TP>现价>SL，当前 TP={tp_price}, 现价={current_price}, SL={sl_price}"
        return
    if side == "short" and not (tp_price < current_price and sl_price > current_price):
        log.error_message = f"AI止盈止损方向无效: short 需 TP<现价<SL，当前 TP={tp_price}, 现价={current_price}, SL={sl_price}"
        return

    logger.info(f"Using AI-determined TP={tp_price} SL={sl_price} for {symbol}")

    # Place order (only reaches here if TP/SL validation passed)
    order = place_order(symbol, side, quantity, strategy.leverage)
    order_dict = _as_dict(order)
    log.order_placed = True
    log.order_id = order_dict.get("id", "")

    # Place TP/SL orders
    tpsl = place_tp_sl(symbol, side, quantity, tp_price, sl_price)
    tpsl_dict = _as_dict(tpsl)

    # Record trade
    trade = Trade(
        strategy_id=strategy.id,
        symbol=symbol,
        side=side,
        action="open",
        price=current_price,
        quantity=quantity,
        order_id=order_dict.get("id", ""),
        tp_price=tp_price,
        sl_price=sl_price,
        tp_order_id=tpsl_dict.get("tp_order_id", ""),
        sl_order_id=tpsl_dict.get("sl_order_id", ""),
        fee=_fee_cost(order_dict),
    )
    db.add(trade)
    db.commit()

    logger.info(f"Opened {side} {symbol} qty={quantity} price={current_price} TP={tp_price} SL={sl_price}")

    # Send Telegram open-position notification (non-blocking, errors are swallowed)
    try:
        from app.telegram_notify import send_open_notification
        send_open_notification(
            symbol=symbol,
            side=side,
            open_price=current_price,
            tp_price=tp_price,
            sl_price=sl_price,
            reason=log.parsed_reasoning or "",
            strategy_name=strategy.name,
        )
    except Exception as _tg_err:
        logger.warning(f"Telegram notification error: {_tg_err}")


def _execute_close(db, strategy: Strategy, symbol: str, side: str,
                    all_positions: list, log: StrategyLog):
    """Execute a close position order."""
    def _norm_side(p: dict) -> str:
        raw = str(p.get("side", "")).lower()
        if raw in ("long", "buy"):
            return "long"
        if raw in ("short", "sell"):
            return "short"
        info = p.get("info", {}) if isinstance(p, dict) else {}
        pos_side = str(info.get("posSide", "")).lower() if isinstance(info, dict) else ""
        if pos_side in ("long", "short"):
            return pos_side
        return raw

    def _pos_amount(p: dict) -> float:
        return abs(_to_float(
            p.get("contracts")
            or p.get("positionAmt")
            or (p.get("info", {}) or {}).get("pos")
            or 0
        ))

    target_pos = None
    for p in all_positions:
        if p.get("symbol") == symbol and _norm_side(p) == side:
            target_pos = p
            break

    if not target_pos or _pos_amount(target_pos) <= 0:
        # No position to close is a harmless no-op; record as hold to avoid noisy errors.
        log.parsed_action = "hold"
        base_reason = log.parsed_reasoning or ""
        extra = f"当前无{side}方向持仓，已忽略平仓指令"
        log.parsed_reasoning = f"{base_reason}；{extra}" if base_reason else extra
        log.error_message = ""
        return

    amount = _pos_amount(target_pos)
    order = close_position(symbol, side, amount)
    order_dict = _as_dict(order)
    log.order_placed = True
    log.order_id = order_dict.get("id", "")

    entry_price = float(target_pos.get("entryPrice", 0))
    close_price = _to_float(order_dict.get("price"), 0.0) or _to_float(target_pos.get("markPrice"), 0.0)
    if side == "long":
        pnl = (close_price - entry_price) * amount
    else:
        pnl = (entry_price - close_price) * amount

    trade = Trade(
        strategy_id=strategy.id,
        symbol=symbol,
        side=side,
        action="close",
        price=close_price,
        quantity=amount,
        order_id=order_dict.get("id", ""),
        pnl=round(pnl, 4),
        fee=_fee_cost(order_dict),
    )
    db.add(trade)
    db.commit()

    logger.info(f"Closed {side} {symbol} qty={amount} pnl={pnl:.4f}")


def sync_positions():
    """Sync positions from OKX to local database."""
    from app.exchange import fetch_position_tpsl
    db = SessionLocal()
    try:
        exchange_positions = fetch_positions()

        # Clear old positions
        db.query(Position).delete()

        for p in exchange_positions:
            raw_symbol = p.get("symbol", "")
            symbol = _normalize_symbol(raw_symbol) or raw_symbol
            side = _normalize_side(p.get("side", ""))
            contracts = abs(_to_float(p.get("contracts"), 0.0))
            if contracts <= 0:
                continue

            # Fetch current TP/SL directly from OKX for this position
            tp_price, sl_price = fetch_position_tpsl(symbol, side)

            pos = Position(
                symbol=symbol,
                side=side,
                size=contracts,
                avg_price=_to_float(p.get("entryPrice"), 0.0),
                unrealized_pnl=_to_float(p.get("unrealizedPnl"), 0.0),
                leverage=int(_to_float(p.get("leverage"), 1) or 1),
                margin=_to_float(p.get("initialMargin"), 0.0),
                tp_price=tp_price,
                sl_price=sl_price,
            )
            
            # Try to find strategy_id from recent open trades (plain string comparison)
            matched_trade = db.query(Trade).filter(
                Trade.action == "open",
                Trade.symbol == symbol,
                Trade.side == side,
            ).order_by(Trade.created_at.desc()).first()
            if matched_trade:
                pos.strategy_id = matched_trade.strategy_id
            
            db.add(pos)

        db.commit()
    except Exception as e:
        logger.error(f"Sync positions error: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def record_equity_snapshot():
    """Record equity for chart."""
    db = SessionLocal()
    try:
        balance = fetch_balance()
        if not balance:
            return
        equity, available = _extract_usdt_balance(balance)
        if equity > 0:
            snap = EquitySnapshot(total_equity=equity, available_balance=available)
            db.add(snap)
            db.commit()
    except Exception as e:
        logger.error(f"Equity snapshot error: {e}")
        db.rollback()
    finally:
        db.close()
