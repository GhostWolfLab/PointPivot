"""Page routes - serve Jinja2 templates"""

import json
import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models import (
    Strategy, StrategyLog, Trade, Position, EquitySnapshot, SystemConfig, get_db,
)
from app.routers.strategies import DEFAULT_PROMPT_TEMPLATE

router = APIRouter(tags=["pages"])

LOGIN_USERNAME = "snowwolf"
LOGIN_PASSWORD = "123qwe"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ─── Template helpers ────────────────────────────────

def _action_badge_class(action: str) -> str:
    mapping = {
        "long": "badge badge-action-long",
        "short": "badge badge-action-short",
        "close_long": "badge badge-action-close_long",
        "close_short": "badge badge-action-close_short",
        "hold": "badge badge-action-hold",
    }
    return mapping.get(action, "badge bg-light text-dark")


def _action_label(action: str) -> str:
    mapping = {
        "long": "做多", "short": "做空",
        "close_long": "平多", "close_short": "平空",
        "hold": "持有",
    }
    return mapping.get(action, action or "-")


def _strategy_name_map(db: Session) -> dict:
    strategies = db.query(Strategy.id, Strategy.name).all()
    return {s.id: s.name for s in strategies}


def _fmt_bj_time(dt: datetime | None, fmt: str = "%m-%d %H:%M:%S") -> str:
    """Format datetime as Beijing time (Asia/Shanghai)."""
    if not dt:
        return "-"
    # Stored DB time is treated as UTC when tzinfo is absent.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    bj = dt.astimezone(ZoneInfo("Asia/Shanghai"))
    return bj.strftime(fmt)


# ─── Page Routes ─────────────────────────────────────

@router.get("/login")
def login_page(request: Request, next: str = "/strategies"):
    if request.session.get("user") == LOGIN_USERNAME:
        return RedirectResponse(url=next or "/strategies", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "next": next or "/strategies",
        "error": "",
    })


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/strategies"),
):
    if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
        request.session["user"] = LOGIN_USERNAME
        safe_next = next if next.startswith("/") else "/strategies"
        return RedirectResponse(url=safe_next or "/strategies", status_code=303)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "next": next or "/strategies",
        "error": "用户名或密码错误",
    }, status_code=400)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

@router.get("/")
def index():
    return RedirectResponse(url="/strategies")


@router.get("/strategies")
def strategies_page(request: Request, db: Session = Depends(get_db)):
    all_strategies = db.query(Strategy).order_by(Strategy.id.desc()).all()
    # Decode symbols JSON for template
    for s in all_strategies:
        if not hasattr(s, '_symbols_cache'):
            s._symbols_cache = s.symbols  # uses the @property
    return templates.TemplateResponse("strategies.html", {
        "request": request,
        "active_page": "strategies",
        "strategies": all_strategies,
    })


@router.get("/strategies/new")
def strategy_new_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("strategy_edit.html", {
        "request": request,
        "active_page": "strategies",
        "is_new": True,
        "strategy": None,
        "default_prompt": DEFAULT_PROMPT_TEMPLATE,
    })


@router.get("/strategies/{strategy_id}/edit")
def strategy_edit_page(strategy_id: int, request: Request, db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not s:
        return RedirectResponse(url="/strategies")

    # Prepare a view-friendly object
    class StrategyView:
        pass
    sv = StrategyView()
    sv.id = s.id
    sv.name = s.name
    sv.enabled = s.enabled
    sv.ai_provider = s.ai_provider or ""
    sv.ai_base_url = s.ai_base_url or ""
    sv.ai_model = s.ai_model or ""
    sv.has_api_key = bool(s.ai_api_key_enc)
    sv.prompt_template = s.prompt_template
    sv.symbols = s.symbols
    sv.interval_minutes = s.interval_minutes
    sv.max_position_pct = s.max_position_pct
    sv.leverage = s.leverage
    sv.take_profit_pct = s.take_profit_pct
    sv.stop_loss_pct = s.stop_loss_pct
    sv.scan_enabled = s.scan_enabled or False
    sv.scan_top_n = s.scan_top_n or 5
    sv.scan_min_change_pct = s.scan_min_change_pct or 3.0
    sv.scan_min_volume_usdt = s.scan_min_volume_usdt or 500000
    try:
        sv.scan_exclude_symbols = json.loads(s.scan_exclude_symbols or "[]")
    except (json.JSONDecodeError, TypeError):
        sv.scan_exclude_symbols = []

    return templates.TemplateResponse("strategy_edit.html", {
        "request": request,
        "active_page": "strategies",
        "is_new": False,
        "strategy": sv,
        "default_prompt": DEFAULT_PROMPT_TEMPLATE,
    })


@router.get("/logs")
def logs_page(
    request: Request,
    strategy_id: int | None = None,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    page_size = 20
    q = db.query(StrategyLog).order_by(StrategyLog.id.desc())
    if strategy_id:
        q = q.filter(StrategyLog.strategy_id == strategy_id)
    total = q.count()
    total_pages = max(1, math.ceil(total / page_size))
    logs = q.offset((page - 1) * page_size).limit(page_size).all()

    strategies = db.query(Strategy).order_by(Strategy.id.desc()).all()
    strategy_names = _strategy_name_map(db)

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "active_page": "logs",
        "logs": logs,
        "strategies": strategies,
        "strategy_names": strategy_names,
        "filter_strategy_id": strategy_id,
        "page": page,
        "total_pages": total_pages,
        "action_badge_class": _action_badge_class,
        "action_label": _action_label,
        "fmt_bj_time": _fmt_bj_time,
    })


@router.get("/positions")
def positions_page(request: Request, db: Session = Depends(get_db)):
    positions = db.query(Position).all()
    strategy_names = _strategy_name_map(db)

    cfg = db.query(SystemConfig).first()
    is_simulated = bool(cfg.okx_simulated) if cfg else True

    equity_data = db.query(EquitySnapshot).order_by(
        EquitySnapshot.timestamp.desc()
    ).limit(288).all()[::-1]

    equity_json = json.dumps([
        {"timestamp": e.timestamp.isoformat(), "total_equity": e.total_equity}
        for e in equity_data
    ])

    return templates.TemplateResponse("positions.html", {
        "request": request,
        "active_page": "positions",
        "positions": positions,
        "strategy_names": strategy_names,
        "equity_data": equity_data,
        "equity_json": equity_json,
        "is_simulated": is_simulated,
    })


@router.get("/trades")
def trades_page(
    request: Request,
    strategy_id: int | None = None,
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    page_size = 50
    q = db.query(Trade).order_by(Trade.id.desc())
    if strategy_id:
        q = q.filter(Trade.strategy_id == strategy_id)
    total = q.count()
    total_pages = max(1, math.ceil(total / page_size))
    trades = q.offset((page - 1) * page_size).limit(page_size).all()

    total_pnl = sum(t.pnl or 0 for t in trades)
    win_count = sum(1 for t in trades if t.pnl and t.pnl > 0)
    loss_count = sum(1 for t in trades if t.pnl and t.pnl < 0)

    strategies = db.query(Strategy).order_by(Strategy.id.desc()).all()
    strategy_names = _strategy_name_map(db)

    return templates.TemplateResponse("trades.html", {
        "request": request,
        "active_page": "trades",
        "trades": trades,
        "strategies": strategies,
        "strategy_names": strategy_names,
        "filter_strategy_id": strategy_id,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "total_pnl": total_pnl,
        "win_count": win_count,
        "loss_count": loss_count,
    })


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    cfg = db.query(SystemConfig).first()
    if not cfg:
        cfg = SystemConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)

    class ConfigView:
        pass
    cv = ConfigView()
    cv.okx_live_api_key_set = bool(cfg.okx_api_key_enc)
    cv.okx_live_secret_key_set = bool(cfg.okx_secret_key_enc)
    cv.okx_live_passphrase_set = bool(cfg.okx_passphrase_enc)
    cv.okx_sim_api_key_set = bool(cfg.okx_sim_api_key_enc)
    cv.okx_sim_secret_key_set = bool(cfg.okx_sim_secret_key_enc)
    cv.okx_sim_passphrase_set = bool(cfg.okx_sim_passphrase_enc)
    cv.okx_simulated = cfg.okx_simulated
    cv.okx_proxy = cfg.okx_proxy or ""
    cv.ai_default_provider = cfg.ai_default_provider or "deepseek"
    cv.ai_default_api_key_set = bool(cfg.ai_default_api_key_enc)
    cv.ai_default_base_url = cfg.ai_default_base_url or ""
    cv.ai_default_model = cfg.ai_default_model or "deepseek-chat"

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active_page": "settings",
        "config": cv,
    })


@router.get("/prompt-templates")
def prompt_templates_page(request: Request):
    return templates.TemplateResponse("prompt_templates.html", {
        "request": request,
        "active_page": "prompt_templates",
    })
