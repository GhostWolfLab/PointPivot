"""OKX AI Auto-Trading System - Main Application"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.models import init_db
from app.engine import start_scheduler, stop_scheduler
from app.routers import strategies, trading, system, pages, prompt_templates

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    init_db()
    logger.info("Starting scheduler...")
    start_scheduler()
    logger.info("System ready")
    yield
    # Shutdown
    logger.info("Stopping scheduler...")
    stop_scheduler()


app = FastAPI(
    title="OKX AI Auto-Trading System",
    version="1.0.0",
    lifespan=lifespan,
)


class AuthRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        public_paths = {
            "/login",
            "/logout",
            "/api/health",
        }
        if path in public_paths or path.startswith("/static/"):
            return await call_next(request)

        if request.session.get("user") == "snowwolf":
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录或登录已失效"}, status_code=401)

        next_path = request.url.path
        if request.url.query:
            next_path += f"?{request.url.query}"
        return RedirectResponse(url=f"/login?next={next_path}", status_code=303)


app.add_middleware(
    AuthRequiredMiddleware,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("APP_SESSION_SECRET", "snowwolf-login-session-secret"),
    same_site="lax",
    https_only=False,
)

# Static files
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Templates
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# API routes
app.include_router(strategies.router)
app.include_router(trading.router)
app.include_router(system.router)
app.include_router(prompt_templates.router)

# Page routes (Jinja2 templates)
app.include_router(pages.router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}
