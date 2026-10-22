"""Telegram bot notification for open position events."""

import logging
import httpx

from app.models import SessionLocal, SystemConfig
from app.config import decrypt_value

logger = logging.getLogger(__name__)

_TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _load_tg_config() -> dict:
    """Load Telegram config from DB."""
    db = SessionLocal()
    try:
        cfg = db.query(SystemConfig).first()
        if not cfg:
            return {}
        return {
            "token": decrypt_value(cfg.tg_bot_token_enc or ""),
            "chat_id": cfg.tg_chat_id or "",
            "proxy": cfg.tg_proxy or "",
        }
    finally:
        db.close()


def send_open_notification(
    symbol: str,
    side: str,
    open_price: float,
    tp_price: float,
    sl_price: float,
    reason: str = "",
    strategy_name: str = "",
) -> None:
    """Send open-position notification via Telegram bot. Silently skips if not configured."""
    cfg = _load_tg_config()
    token = cfg.get("token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return

    direction_icon = "📈" if side == "long" else "📉"
    direction_label = "做多 (Long)" if side == "long" else "做空 (Short)"

    text = (
        f"🔔 <b>开仓通知</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>策略：</b>{strategy_name or '-'}\n"
        f"<b>交易对：</b><code>{symbol}</code>\n"
        f"<b>方向：</b>{direction_icon} {direction_label}\n"
        f"<b>开仓价：</b><code>{open_price}</code>\n"
        f"<b>止盈价：</b><code>{tp_price}</code>\n"
        f"<b>止损价：</b><code>{sl_price}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>理由：</b>{reason or '-'}"
    )

    url = _TELEGRAM_SEND_URL.format(token=token)
    proxy = cfg.get("proxy", "")
    proxies = {"all://": proxy} if proxy else None

    try:
        with httpx.Client(proxies=proxies, timeout=10) as client:
            resp = client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            result = resp.json()
            if not result.get("ok"):
                logger.warning(f"Telegram notification failed: {result}")
    except Exception as e:
        logger.warning(f"Telegram notification error: {e}")


def test_notification(token: str, chat_id: str, proxy: str = "") -> tuple[bool, str]:
    """Send a test message. Returns (ok, message)."""
    if not token or not chat_id:
        return False, "Bot Token 和 Chat ID 不能为空"

    text = "✅ PointPivot 交易系统 Telegram 通知测试成功！"
    url = _TELEGRAM_SEND_URL.format(token=token)
    proxies = {"all://": proxy} if proxy else None

    try:
        with httpx.Client(proxies=proxies, timeout=10) as client:
            resp = client.post(url, json={
                "chat_id": chat_id,
                "text": text,
            })
            result = resp.json()
            if result.get("ok"):
                return True, "测试消息发送成功"
            desc = result.get("description", str(result))
            return False, f"发送失败: {desc}"
    except Exception as e:
        return False, f"请求异常: {e}"
