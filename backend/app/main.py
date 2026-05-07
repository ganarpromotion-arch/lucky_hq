"""
Lucky HQ FastAPI 진입점
"""
import logging
import os
import time

# 타임존: Asia/Seoul — Railway가 TZ 미설정 시에도 본부 시간을 KST로 통일
os.environ.setdefault("TZ", "Asia/Seoul")
try:
    time.tzset()
except AttributeError:
    pass  # Windows는 tzset 없음 (Railway는 Linux이므로 영향 없음)

from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine, SessionLocal
from .config import get_settings
from .routers import console as console_router
from .routers import music as music_router
from .routers import settings as settings_router
from .routers import batch as batch_router
from .routers import telegram as telegram_router
from .seed import run_seed


FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
log = logging.getLogger("lucky_hq")


async def _register_telegram_webhook() -> None:
    """배포 시작 시 텔레그램에 webhook 등록.
    Railway가 RAILWAY_PUBLIC_DOMAIN 환경변수로 외부 도메인 알려줌."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_webhook_secret:
        log.info("텔레그램 webhook 등록 건너뜀 (토큰 또는 secret 미설정)")
        return

    public_domain = (
        os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        or os.environ.get("PUBLIC_DOMAIN")
        or ""
    ).strip()
    if not public_domain:
        log.info("텔레그램 webhook 등록 건너뜀 (RAILWAY_PUBLIC_DOMAIN 없음 — 로컬 추정)")
        return

    if not public_domain.startswith("http"):
        public_domain = f"https://{public_domain}"
    webhook_url = f"{public_domain.rstrip('/')}/api/telegram/webhook"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{settings.telegram_api_base}/bot{settings.telegram_bot_token}/setWebhook",
                json={
                    "url": webhook_url,
                    "secret_token": settings.telegram_webhook_secret,
                    "allowed_updates": ["message", "edited_message"],
                    "drop_pending_updates": False,
                },
            )
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if data.get("ok"):
                log.info(f"텔레그램 webhook 등록 완료: {webhook_url}")
            else:
                log.warning(f"텔레그램 webhook 등록 실패: {data}")
    except Exception as e:
        log.warning(f"텔레그램 webhook 등록 예외: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) 테이블 생성 (V1: alembic 없이 단순 create_all)
    Base.metadata.create_all(bind=engine)
    # 2) 시드
    run_seed()
    # 3) 텔레그램 webhook 등록 (env 있으면)
    await _register_telegram_webhook()
    yield


app = FastAPI(title="Lucky HQ", version="1.0.0", lifespan=lifespan)

# API
app.include_router(console_router.router)
app.include_router(music_router.router)
app.include_router(settings_router.router)
app.include_router(batch_router.router)
app.include_router(telegram_router.router)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "lucky_hq"}


# 정적 자산
app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR / "static")),
    name="static",
)


# 페이지 라우팅 (간단 라우터)
@app.get("/")
def page_root():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/dept/{slug}")
def page_department(slug: str):
    target = FRONTEND_DIR / f"{slug}.html"
    if target.exists():
        return FileResponse(target)
    return JSONResponse({"error": f"부서 페이지 없음: {slug}"}, status_code=404)


@app.get("/secrets")
def page_secrets():
    return FileResponse(FRONTEND_DIR / "secrets.html")
