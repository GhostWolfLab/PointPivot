"""System configuration API routes"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import SystemConfig, get_db
from app.schemas import SystemConfigUpdate, SystemConfigOut
from app.config import encrypt_value
from app.exchange import fetch_balance, get_exchange
from app.engine import _extract_usdt_balance

router = APIRouter(prefix="/api/system", tags=["system"])


class PositionModeUpdate(BaseModel):
    mode: Literal["net_mode", "long_short_mode"]


def _position_mode_label(mode: str) -> str:
    return "双向持仓" if mode == "long_short_mode" else "单向持仓"


def _is_okx_mode_switch_blocked(error_text: str) -> bool:
    return '"code":"59000"' in error_text or "Setting failed. Cancel any open orders" in error_text


@router.get("/config", response_model=SystemConfigOut)
def get_config(db: Session = Depends(get_db)):
    cfg = db.query(SystemConfig).first()
    if not cfg:
        cfg = SystemConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

    return _build_config_out(cfg)


def _build_config_out(cfg) -> SystemConfigOut:
    return SystemConfigOut(
        okx_live_api_key_set=bool(cfg.okx_api_key_enc),
        okx_live_secret_key_set=bool(cfg.okx_secret_key_enc),
        okx_live_passphrase_set=bool(cfg.okx_passphrase_enc),
        okx_sim_api_key_set=bool(cfg.okx_sim_api_key_enc),
        okx_sim_secret_key_set=bool(cfg.okx_sim_secret_key_enc),
        okx_sim_passphrase_set=bool(cfg.okx_sim_passphrase_enc),
        okx_simulated=cfg.okx_simulated,
        okx_proxy=cfg.okx_proxy or "",
        ai_default_provider=cfg.ai_default_provider or "deepseek",
        ai_default_api_key_set=bool(cfg.ai_default_api_key_enc),
        ai_default_base_url=cfg.ai_default_base_url or "",
        ai_default_model=cfg.ai_default_model or "deepseek-chat",
        tg_bot_token_set=bool(cfg.tg_bot_token_enc),
        tg_chat_id=cfg.tg_chat_id or "",
        tg_proxy=cfg.tg_proxy or "",
        updated_at=cfg.updated_at,
    )


@router.put("/config", response_model=SystemConfigOut)
def update_config(data: SystemConfigUpdate, db: Session = Depends(get_db)):
    cfg = db.query(SystemConfig).first()
    if not cfg:
        cfg = SystemConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

    if data.okx_live_api_key is not None and data.okx_live_api_key:
        cfg.okx_api_key_enc = encrypt_value(data.okx_live_api_key)
    if data.okx_live_secret_key is not None and data.okx_live_secret_key:
        cfg.okx_secret_key_enc = encrypt_value(data.okx_live_secret_key)
    if data.okx_live_passphrase is not None and data.okx_live_passphrase:
        cfg.okx_passphrase_enc = encrypt_value(data.okx_live_passphrase)
    if data.okx_sim_api_key is not None and data.okx_sim_api_key:
        cfg.okx_sim_api_key_enc = encrypt_value(data.okx_sim_api_key)
    if data.okx_sim_secret_key is not None and data.okx_sim_secret_key:
        cfg.okx_sim_secret_key_enc = encrypt_value(data.okx_sim_secret_key)
    if data.okx_sim_passphrase is not None and data.okx_sim_passphrase:
        cfg.okx_sim_passphrase_enc = encrypt_value(data.okx_sim_passphrase)
    if data.okx_simulated is not None:
        cfg.okx_simulated = data.okx_simulated
    if data.okx_proxy is not None:
        cfg.okx_proxy = data.okx_proxy
    if data.ai_default_provider is not None:
        cfg.ai_default_provider = data.ai_default_provider
    if data.ai_default_api_key is not None and data.ai_default_api_key:
        cfg.ai_default_api_key_enc = encrypt_value(data.ai_default_api_key)
    if data.ai_default_base_url is not None:
        cfg.ai_default_base_url = data.ai_default_base_url
    if data.ai_default_model is not None:
        cfg.ai_default_model = data.ai_default_model
    if data.tg_bot_token is not None and data.tg_bot_token:
        cfg.tg_bot_token_enc = encrypt_value(data.tg_bot_token)
    if data.tg_chat_id is not None:
        cfg.tg_chat_id = data.tg_chat_id
    if data.tg_proxy is not None:
        cfg.tg_proxy = data.tg_proxy

    db.commit()
    db.refresh(cfg)

    return _build_config_out(cfg)


@router.post("/test-connection")
def test_connection(db: Session = Depends(get_db)):
    """Test connectivity with the currently active API credentials."""
    cfg = db.query(SystemConfig).first()
    mode = "模拟盘" if cfg and cfg.okx_simulated else "实盘"
    try:
        # Call exchange directly so real exceptions are not swallowed
        exchange = get_exchange()
        balance = exchange.fetch_balance({"type": "swap"})

        equity, available = _extract_usdt_balance(balance)

        # If still 0, try to extract from totalEq in info.data[0]
        if equity == 0.0:
            info = balance.get("info", {})
            data = info.get("data", []) if isinstance(info, dict) else []
            if data and isinstance(data[0], dict):
                try:
                    from app.engine import _to_float
                    equity = _to_float(data[0].get("totalEq")) or 0.0
                    available = _to_float(data[0].get("adjEq")) or equity
                except Exception:
                    pass

        # Collect debug info
        debug = {}
        info = balance.get("info", {})
        data = info.get("data", []) if isinstance(info, dict) else []
        if data and isinstance(data[0], dict):
            d0 = data[0]
            debug["totalEq"] = d0.get("totalEq")
            debug["adjEq"] = d0.get("adjEq")
            details = d0.get("details", [])
            debug["details"] = [
                {k: v for k, v in d.items() if k in ("ccy", "eq", "eqUsd", "cashBal", "availEq", "availBal")}
                for d in (details[:5] if isinstance(details, list) else [])
            ]
        debug["top_keys"] = list(balance.keys())
        debug["total"] = balance.get("total")
        debug["free_usdt"] = (balance.get("free") or {}).get("USDT")

        return {
            "ok": True,
            "mode": mode,
            "equity": equity,
            "available": available,
            "message": f"{mode} API 连接成功，权益 {equity:.2f} USDT，可用 {available:.2f} USDT",
            "debug": debug,
        }
    except Exception as e:
        return {
            "ok": False,
            "mode": mode,
            "equity": 0.0,
            "available": 0.0,
            "message": f"{mode} API 连接失败：{str(e)}",
        }


@router.get("/position-mode")
def get_position_mode():
    """Get current OKX position mode: net_mode or long_short_mode."""
    try:
        exchange = get_exchange()
        resp = exchange.privateGetAccountConfig()
        data = resp.get("data", []) if isinstance(resp, dict) else []
        mode = "net_mode"
        if data and isinstance(data[0], dict):
            mode = data[0].get("posMode") or "net_mode"
        return {
            "ok": True,
            "mode": mode,
            "mode_label": _position_mode_label(mode),
            "raw": resp,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取持仓模式失败: {str(e)}")


@router.put("/position-mode")
def set_position_mode(data: PositionModeUpdate):
    """Switch OKX position mode between net_mode and long_short_mode."""
    try:
        exchange = get_exchange()
        resp = exchange.privatePostAccountSetPositionMode({"posMode": data.mode})
        code = str(resp.get("code", "")) if isinstance(resp, dict) else ""
        if code not in ("0", ""):
            raise HTTPException(status_code=400, detail=f"切换持仓模式失败: {resp}")

        latest = exchange.privateGetAccountConfig()
        latest_data = latest.get("data", []) if isinstance(latest, dict) else []
        mode = data.mode
        if latest_data and isinstance(latest_data[0], dict):
            mode = latest_data[0].get("posMode") or data.mode

        return {
            "ok": True,
            "mode": mode,
            "mode_label": _position_mode_label(mode),
            "message": f"已切换为{_position_mode_label(mode)}",
        }
    except HTTPException:
        raise
    except Exception as e:
        error_text = str(e)
        if _is_okx_mode_switch_blocked(error_text):
            raise HTTPException(
                status_code=400,
                detail=(
                    "OKX 不允许切换持仓模式：请先取消所有挂单、平掉全部持仓，并停止交易机器人后重试。"
                ),
            )
        raise HTTPException(status_code=500, detail=f"切换持仓模式失败: {error_text}")


class TelegramTestRequest(BaseModel):
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    tg_proxy: str = ""


@router.post("/test-telegram")
def test_telegram(data: TelegramTestRequest, db: Session = Depends(get_db)):
    """Send a Telegram test message using the provided (or saved) credentials."""
    from app.telegram_notify import test_notification
    from app.config import decrypt_value

    token = data.tg_bot_token.strip()
    chat_id = data.tg_chat_id.strip()
    proxy = data.tg_proxy.strip()

    # Fall back to saved config if fields are empty
    if not token or not chat_id:
        cfg = db.query(SystemConfig).first()
        if cfg:
            if not token:
                token = decrypt_value(cfg.tg_bot_token_enc or "")
            if not chat_id:
                chat_id = cfg.tg_chat_id or ""
            if not proxy:
                proxy = cfg.tg_proxy or ""

    ok, message = test_notification(token, chat_id, proxy)
    return {"ok": ok, "message": message}

