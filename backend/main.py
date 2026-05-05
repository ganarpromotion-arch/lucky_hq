"""Lucky HQ FastAPI 진입점."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.db import init_db
from backend.agents.registry import load_builtins
from backend.core.scheduler import start as sched_start, stop as sched_stop, reload_all
from backend.api import agents as agents_api
from backend.api import commander as commander_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 직원 모듈 로드 (@register 발동)
    load_builtins()
    # 2. DB 테이블 생성
    init_db()
    # 3. 스케줄러 부팅 + DB의 직원 cron 등록
    sched_start()
    reload_all()
    yield
    sched_stop()


app = FastAPI(title="Lucky HQ", version="0.1.0", lifespan=lifespan)

app.include_router(agents_api.router)
app.include_router(commander_api.router)


@app.get("/health")
def health():
    return {"ok": True, "service": "lucky-hq"}


# ─── 정적 프론트엔드 ───────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"

if FRONT.exists():
    app.mount("/static", StaticFiles(directory=str(FRONT)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(FRONT / "index.html"))
