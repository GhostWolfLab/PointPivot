"""OKX exchange client via ccxt"""

import logging
import urllib3
import ccxt
from app.config import decrypt_value
from app.models import SessionLocal, SystemConfig

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger(__name__)


def _is_pos_side_error(error: Exception) -> bool:
    message = str(error)
    return "Parameter posSide error" in message or '"sCode":"51000"' in message


def _load_okx_config() -> dict:
    """Load OKX credentials from DB (encrypted)"""
    db = SessionLocal()
    try:
        cfg = db.query(SystemConfig).first()
        if not cfg:
            return {}

        use_sim = bool(cfg.okx_simulated)
        if use_sim:
            api_key = decrypt_value(cfg.okx_sim_api_key_enc)
            secret = decrypt_value(cfg.okx_sim_secret_key_enc)
            password = decrypt_value(cfg.okx_sim_passphrase_enc)
            # 兼容历史数据：模拟盘若未单独配置，回退到旧字段
            if not api_key:
                api_key = decrypt_value(cfg.okx_api_key_enc)
            if not secret:
                secret = decrypt_value(cfg.okx_secret_key_enc)
            if not password:
                password = decrypt_value(cfg.okx_passphrase_enc)
        else:
            api_key = decrypt_value(cfg.okx_api_key_enc)
            secret = decrypt_value(cfg.okx_secret_key_enc)
            password = decrypt_value(cfg.okx_passphrase_enc)

        return {
            "api_key": api_key,
            "secret": secret,
            "password": password,
            "simulated": use_sim,
            "proxy": cfg.okx_proxy or "",
        }
    finally:
        db.close()


# 缓存 exchange 实例，避免每次都 load_markets
_exchange_cache: ccxt.okx | None = None
_exchange_proxy: str = ""


def get_exchange(load_markets: bool = True) -> ccxt.okx:
    """Create or reuse an OKX exchange instance."""
    global _exchange_cache, _exchange_proxy

    creds = _load_okx_config()
    options = {"defaultType": "swap"}
    headers = {}
    if creds.get("simulated", True):
        headers["x-simulated-trading"] = "1"

    proxy = creds.get("proxy", "")

    # 如果代理配置变了，清除缓存
    if _exchange_cache is not None and proxy != _exchange_proxy:
        _exchange_cache = None

    if _exchange_cache is not None:
        # 更新凭证（可能被修改）
        _exchange_cache.apiKey = creds.get("api_key", "")
        _exchange_cache.secret = creds.get("secret", "")
        _exchange_cache.password = creds.get("password", "")
        _exchange_cache.headers = headers
        return _exchange_cache

    config = {
        "apiKey": creds.get("api_key", ""),
        "secret": creds.get("secret", ""),
        "password": creds.get("password", ""),
        "options": options,
        "headers": headers,
        "enableRateLimit": True,
    }

    exchange = ccxt.okx(config)
    # 跳过私有 currencies 端点，避免无 API Key 时 load_markets 失败
    exchange.has["fetchCurrencies"] = False

    # 通过底层 requests.Session 设置代理，绕过 ccxt 自身代理冲突问题
    if proxy:
        exchange.session.proxies = {
            "http": proxy,
            "https": proxy,
        }
        # 代理环境下禁用 SSL 验证（Clash 等代理的 MITM 会导致 SSL 握手失败）
        exchange.session.verify = False

    if not load_markets:
        return exchange

    # 只加载 SWAP 市场，避免 ccxt 解析 SPOT 市场时的 NoneType bug
    try:
        # 手动获取并解析，跳过解析失败的条目
        resp = exchange.publicGetPublicInstruments({"instType": "SWAP"})
        raw_markets = resp.get("data", [])
        parsed = []
        for item in raw_markets:
            try:
                parsed.append(exchange.parse_market(item))
            except Exception:
                continue
        exchange.set_markets(parsed)
        logger.info(f"Loaded {len(parsed)} SWAP markets")
    except Exception as e:
        logger.error(f"Failed to load SWAP markets: {e}")
        # 市场加载失败，不缓存，下次重试
        return exchange

    _exchange_cache = exchange
    _exchange_proxy = proxy
    return exchange


def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 20) -> list:
    """Fetch OHLCV candles. Returns list of [timestamp, open, high, low, close, volume]."""
    try:
        exchange = get_exchange()
        candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return candles
    except Exception as e:
        logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
        return []


def fetch_ticker(symbol: str) -> dict:
    """Fetch current ticker info"""
    try:
        exchange = get_exchange()
        return exchange.fetch_ticker(symbol)
    except Exception as e:
        logger.error(f"Failed to fetch ticker for {symbol}: {e}")
        return {}


def fetch_balance() -> dict:
    """Fetch account balance"""
    exchange = get_exchange()
    try:
        return exchange.fetch_balance({"type": "swap"})
    except Exception as e:
        logger.warning(f"Fetch balance by type=swap failed, fallback to default: {e}")
        try:
            return exchange.fetch_balance()
        except Exception as e2:
            logger.error(f"Failed to fetch balance: {e2}")
            return {}


def fetch_positions() -> list:
    """Fetch all open positions"""
    try:
        exchange = get_exchange()
        positions = exchange.fetch_positions()
        return [p for p in positions if p.get("contracts", 0) and float(p["contracts"]) > 0]
    except Exception as e:
        logger.error(f"Failed to fetch positions: {e}")
        return []


def fetch_position_tpsl(symbol: str, side: str) -> tuple[float | None, float | None]:
    """Fetch current TP/SL prices for a specific position from OKX algo orders.
    Returns (tp_price, sl_price) or (None, None) if not found.
    """
    try:
        exchange = get_exchange(load_markets=False)
        # Fetch pending algo orders (TP/SL orders)
        resp = exchange.privateGetTradePendingAlgoOrders({
            "instType": "SWAP",
            "instId": symbol,
            "ordType": "conditional",
        })
        data = resp.get("data", []) or []
        
        # Match by symbol and position side
        tp_price = None
        sl_price = None
        for order in data:
            order_side = order.get("posSide", "")
            if order_side.lower() != side.lower():
                continue
            
            # Check if this is TP or SL based on tpTriggerPx/slTriggerPx
            if order.get("tpTriggerPx"):
                tp_price = float(order.get("tpTriggerPx", 0))
            if order.get("slTriggerPx"):
                sl_price = float(order.get("slTriggerPx", 0))
        
        return tp_price, sl_price
    except Exception as e:
        logger.debug(f"Failed to fetch TP/SL for {symbol} {side}: {e}")
        return None, None


def set_leverage(symbol: str, leverage: int, side: str = "long"):
    """Set leverage for a symbol"""
    try:
        exchange = get_exchange()
        exchange.set_leverage(leverage, symbol, params={"mgnMode": "isolated", "posSide": side})
    except Exception as e:
        if _is_pos_side_error(e):
            try:
                exchange = get_exchange()
                exchange.set_leverage(leverage, symbol, params={"mgnMode": "isolated"})
                return
            except Exception as e2:
                logger.warning(f"Set leverage for {symbol} failed in net mode fallback: {e2}")
                return
        logger.warning(f"Set leverage for {symbol} {side} failed (may already be set): {e}")


def place_order(symbol: str, side: str, amount: float, leverage: int) -> dict:
    """
    Place a market order for perpetual swap.
    side: 'long' or 'short'
    Returns order dict from ccxt.
    """
    exchange = get_exchange()

    # Set leverage for both sides
    set_leverage(symbol, leverage, "long")
    set_leverage(symbol, leverage, "short")

    # Map side to ccxt params
    if side == "long":
        order_side = "buy"
        pos_side = "long"
    else:
        order_side = "sell"
        pos_side = "short"

    params = {
        "tdMode": "isolated",
        "posSide": pos_side,
    }

    try:
        order = exchange.create_market_order(symbol, order_side, amount, params=params)
    except Exception as e:
        if not _is_pos_side_error(e):
            raise
        logger.warning(f"Place order fallback to net mode for {symbol}: {e}")
        order = exchange.create_market_order(
            symbol,
            order_side,
            amount,
            params={"tdMode": "isolated"},
        )
    logger.info(f"Order placed: {symbol} {side} amount={amount} -> {order.get('id')}")
    return order


def close_position(symbol: str, side: str, amount: float) -> dict:
    """
    Close a position by placing a counter order.
    side: 'long' or 'short' (the position side to close)
    """
    exchange = get_exchange()

    if side == "long":
        order_side = "sell"
        pos_side = "long"
    else:
        order_side = "buy"
        pos_side = "short"

    params = {
        "tdMode": "isolated",
        "posSide": pos_side,
    }

    try:
        order = exchange.create_market_order(symbol, order_side, amount, params=params)
    except Exception as e:
        if not _is_pos_side_error(e):
            raise
        logger.warning(f"Close position fallback to net mode for {symbol}: {e}")
        order = exchange.create_market_order(
            symbol,
            order_side,
            amount,
            params={"tdMode": "isolated", "reduceOnly": True},
        )
    logger.info(f"Close position: {symbol} {side} amount={amount} -> {order.get('id')}")
    return order


def place_tp_sl(symbol: str, side: str, size: float, tp_price: float, sl_price: float) -> dict:
    """
    Place take-profit and stop-loss orders (OKX algo order).
    Returns dict with tp_order_id and sl_order_id.
    """
    exchange = get_exchange()
    result = {"tp_order_id": "", "sl_order_id": ""}

    pos_side = side  # "long" or "short"

    # Take Profit
    try:
        if side == "long":
            tp_side = "sell"
        else:
            tp_side = "buy"
        tp_order = exchange.create_order(
            symbol, "market", tp_side, size,
            params={
                "tdMode": "isolated",
                "posSide": pos_side,
                "ordType": "conditional",
                "tpTriggerPx": str(tp_price),
                "tpOrdPx": "-1",  # market price
                "triggerPxType": "last",
            }
        )
        result["tp_order_id"] = tp_order.get("id", "")
    except Exception as e:
        logger.error(f"TP order failed for {symbol}: {e}")
        # Fallback: use ccxt's native TP/SL if available
        try:
            tp_params = {
                "tdMode": "isolated",
                "tpTriggerPx": str(tp_price),
                "tpOrdPx": "-1",
            }
            if not _is_pos_side_error(e):
                tp_params["posSide"] = pos_side
            if side == "long":
                tp_order = exchange.create_order(symbol, "market", "sell", size, params=tp_params)
            else:
                tp_order = exchange.create_order(symbol, "market", "buy", size, params=tp_params)
            result["tp_order_id"] = tp_order.get("id", "")
        except Exception as e2:
            logger.error(f"TP order fallback also failed: {e2}")

    # Stop Loss
    try:
        if side == "long":
            sl_side = "sell"
        else:
            sl_side = "buy"
        sl_order = exchange.create_order(
            symbol, "market", sl_side, size,
            params={
                "tdMode": "isolated",
                "posSide": pos_side,
                "ordType": "conditional",
                "slTriggerPx": str(sl_price),
                "slOrdPx": "-1",
                "triggerPxType": "last",
            }
        )
        result["sl_order_id"] = sl_order.get("id", "")
    except Exception as e:
        logger.error(f"SL order failed for {symbol}: {e}")
        if _is_pos_side_error(e):
            try:
                sl_params = {
                    "tdMode": "isolated",
                    "ordType": "conditional",
                    "slTriggerPx": str(sl_price),
                    "slOrdPx": "-1",
                    "triggerPxType": "last",
                }
                if side == "long":
                    sl_order = exchange.create_order(symbol, "market", "sell", size, params=sl_params)
                else:
                    sl_order = exchange.create_order(symbol, "market", "buy", size, params=sl_params)
                result["sl_order_id"] = sl_order.get("id", "")
            except Exception as e2:
                logger.error(f"SL order fallback also failed: {e2}")

    return result


# ──────────── 涨幅榜扫描 ────────────

def fetch_top_gainers(top_n: int = 10, min_volume_usdt: float = 500_000,
                      exclude_symbols: list | None = None) -> list[dict] | None:
    """
    扫描 OKX SWAP 市场 1 小时涨幅榜。
    返回按涨幅降序排列的 top_n 个币种信息:
    [{"symbol": "XXX-USDT-SWAP", "price": ..., "change_pct_1h": ..., "volume_24h": ...}, ...]

    Args:
        top_n: 返回前 N 个涨幅最大的
        min_volume_usdt: 最低 24h 成交额（USDT），过滤低流动性山寨币
        exclude_symbols: 排除的交易对列表（如 BTC/ETH 等主流币）
    """
    if exclude_symbols is None:
        exclude_symbols = []

    exchange = get_exchange(load_markets=False)
    ticker_rows: list[dict] | None = None
    request_url = "https://www.okx.com/api/v5/market/tickers"
    last_error = None

    for attempt in range(1, 4):
        try:
            response = exchange.session.get(
                request_url,
                params={"instType": "SWAP"},
                headers=exchange.headers,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            code = payload.get("code")
            if code not in (None, 0, "0"):
                raise RuntimeError(f"OKX返回错误 code={code} msg={payload.get('msg', '')}")
            ticker_rows = payload.get("data", []) or []
            break
        except Exception as error:
            last_error = error
            logger.warning(f"Fetch SWAP tickers attempt {attempt}/3 failed: {error}")

    if ticker_rows is None:
        logger.error(f"Failed to fetch tickers for gainers scan after 3 attempts: {last_error}")
        return None

    results = []
    for t in ticker_rows:
        symbol = t.get("instId", "")

        # 只看 USDT 永续合约
        if not symbol.endswith("-USDT-SWAP") and not symbol.endswith("/USDT:USDT"):
            continue
        if symbol in exclude_symbols:
            continue

        # 规范化 symbol 名称为 OKX 格式
        # ccxt 可能返回 "BTC/USDT:USDT"，需要转为 "BTC-USDT-SWAP"
        normalized = symbol
        if "/" in symbol:
            base = symbol.split("/")[0]
            normalized = f"{base}-USDT-SWAP"
        if normalized in exclude_symbols:
            continue

        open_price = t.get("open24h") or t.get("open")
        last_price = t.get("last")
        quote_volume = t.get("volCcy24h") or t.get("quoteVolume") or 0

        open_price = float(open_price) if open_price is not None else 0
        last_price = float(last_price) if last_price is not None else 0
        quote_volume = float(quote_volume) if quote_volume is not None else 0

        if not open_price or not last_price or open_price <= 0:
            continue

        # 过滤低流动性
        if quote_volume < min_volume_usdt:
            continue

        change_pct = ((last_price - open_price) / open_price) * 100

        # 只要涨的（> 1%才有意义）
        if change_pct < 1.0:
            continue

        results.append({
            "symbol": normalized,
            "price": last_price,
            "change_pct_1h": round(change_pct, 2),
            "volume_24h": round(quote_volume, 2),
        })

    # 按涨幅降序排列
    results.sort(key=lambda x: x["change_pct_1h"], reverse=True)
    return results[:top_n]
