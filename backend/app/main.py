"""
Lucky HQ FastAPI 진입점
"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine
from .routers import console as console_router
from .routers import music as music_router
from .seed import run_seed


FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) 테이블 생성 (V1: alembic 없이 단순 create_all)
    Base.metadata.create_all(bind=engine)
    # 2) 시드
    run_seed()
    yield


app = FastAPI(title="Lucky HQ", version="1.0.0", lifespan=lifespan)

# API
app.include_router(console_router.router)
app.include_router(music_router.router)


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
