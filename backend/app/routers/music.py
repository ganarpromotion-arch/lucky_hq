"""
음악제작 부서 API

엔드포인트:
- POST /api/music/generate         : 곡 생성 요청 (Job 생성 + Mureka 호출)
- GET  /api/music/jobs             : 부서 작업 목록
- GET  /api/music/jobs/{job_id}    : 단일 작업 상태 (자동 폴링/갱신)

Mureka 호출은 반드시 api_manager.call_api()를 통해서만 진행한다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import Job, Agent, AuditLog, Batch
from ..api_manager import call_api
from ..songwriter import compose_plan as songwriter_compose
from ..batch_runner import run_daily_batch, check_mureka_balance
from ..curator import curate as curator_curate

router = APIRouter(prefix="/api/music", tags=["music"])


class GenerateRequest(BaseModel):
    lyrics: str = Field(..., min_length=1, max_length=4000)
    style: str = Field(default="pop", max_length=200)
    title: str = Field(default="", max_length=200)


class ComposePlanRequest(BaseModel):
    issue: str = Field(..., min_length=1, max_length=1000)


def _set_agent_status(db: Session, slug: str, status: str) -> None:
    a = db.query(Agent).filter_by(slug=slug).first()
    if a:
        a.current_status = status
        a.last_seen_at = datetime.utcnow()


def _audit(db: Session, action: str, target: str = "", detail: dict | None = None, actor: str = "music_producer") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail or {}))


# ── 작곡가 직원 ──────────────────────────────────────────
@router.post("/compose-plan")
async def compose_plan_endpoint(req: ComposePlanRequest, db: Session = Depends(get_db)):
    """작곡가 직원: 최근 이슈 → 제목/가사/스타일 기획안.
    LLM(Anthropic/OpenAI) 우선, 실패 시 룰 기반 폴백.
    사용자가 결과를 수정한 뒤 generate에 전달."""
    _set_agent_status(db, "songwriter", "기획 중")
    db.commit()

    plan = await songwriter_compose(req.issue, db=db)

    _audit(db, "music.compose_plan", target="songwriter",
           detail={
               "issue_len": len(req.issue),
               "mood": plan.get("mood"),
               "keyword": plan.get("keyword"),
               "source": plan.get("source"),
           },
           actor="songwriter")
    _set_agent_status(db, "songwriter", "대기")
    db.commit()
    return plan


@router.post("/generate")
async def generate(req: GenerateRequest, db: Session = Depends(get_db)):
    # 동시 호출 보호 — 진행 중인 곡이 있으면 거부 (429 폭탄 방지)
    busy = (
        db.query(Job)
        .filter(Job.department_slug == "music",
                Job.status.in_(("pending", "running")))
        .first()
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"이미 진행 중인 곡이 있습니다 (#{busy.id}). 완료 후 다시 시도해주세요."
        )

    # 1) Job 생성
    job = Job(
        kind="music_generate",
        department_slug="music",
        agent_slug="music_producer",
        status="pending",
        input={"lyrics_len": len(req.lyrics), "style": req.style, "title": req.title},
    )
    db.add(job)
    db.flush()

    _set_agent_status(db, "music_producer", "곡 생성 요청 중")
    _audit(db, "music.generate.requested", target=f"job:{job.id}", detail={"style": req.style, "title": req.title})
    db.commit()

    # 2) API 관리 직원 통해 Mureka 호출
    # Mureka 스펙: lyrics + prompt(스타일) + model. title은 Mureka가 받지 않으니 우리 DB에만 보관.
    payload = {
        "lyrics": req.lyrics,
        "prompt": req.style,
        "model": "auto",
    }
    result = await call_api(
        db, provider="mureka", operation="generate",
        payload=payload, requester="music_producer",
    )

    # 3) 결과 반영
    job = db.get(Job, job.id)
    if result.get("ok"):
        data = result.get("data") or {}
        # Mureka 응답: id 필드 (문자열). trace_id 도 같이 옴.
        ext_id = data.get("id") or data.get("task_id") or data.get("song_id")
        job.external_id = str(ext_id) if ext_id else None
        job.status = "running"
        job.output = {"submitted": data}
        _set_agent_status(db, "music_producer", "Mureka 처리 대기")
        _audit(db, "music.generate.submitted", target=f"job:{job.id}", detail={"external_id": ext_id})
    else:
        job.status = "failed"
        job.error = result.get("error", "")
        _set_agent_status(db, "music_producer", "대기")
        _audit(db, "music.generate.failed", target=f"job:{job.id}", detail={"error": job.error})

    db.commit()
    return _serialize_job(job)


@router.get("/mureka-billing")
async def mureka_billing(db: Session = Depends(get_db)):
    """Mureka 계정 잔량/요금제 조회. 키 유효성 + 한도 진단용.
    GET /v1/account/billing 호출 결과를 그대로 돌려준다."""
    result = await call_api(
        db, provider="mureka", operation="billing",
        payload={}, requester="music_producer",
    )
    return {
        "ok": result.get("ok", False),
        "status_code": result.get("status_code", 0),
        "data": result.get("data"),
        "error": result.get("error", ""),
    }


@router.get("/jobs")
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    rows = (
        db.query(Job)
        .filter(Job.department_slug == "music")
        .order_by(desc(Job.created_at))
        .limit(min(limit, 100))
        .all()
    )
    return [_serialize_job(j) for j in rows]


@router.post("/jobs/{job_id}/refresh")
async def refresh_job(job_id: int, db: Session = Depends(get_db)):
    """완료된 곡인데 audio가 안 보일 때 Mureka에 강제 재조회.
    상태에 관계없이 external_id로 query 한 번 더 돌리고 output을 덮어쓴다."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if not job.external_id:
        raise HTTPException(400, "external_id 없음 — Mureka에 제출된 적이 없는 곡")

    result = await call_api(
        db, provider="mureka", operation="query",
        payload={"id": job.external_id}, requester="music_producer",
    )
    if result.get("ok"):
        data = result.get("data") or {}
        mstatus = (data.get("status") or "").lower()
        job.output = data
        if mstatus == "succeeded":
            job.status = "done"
        elif mstatus in {"failed", "error", "timeouted", "cancelled"}:
            job.status = "failed"
            job.error = str(data.get("failed_reason") or data.get("error") or data.get("message") or f"mureka {mstatus}")
    else:
        # 응답이 실패해도 raw 에러는 노출
        job.error = result.get("error", "")
    db.commit()
    return _serialize_job(job)


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")

    # 진행 중이면 Mureka에 polling
    if job.status == "running" and job.external_id:
        result = await call_api(
            db, provider="mureka", operation="query",
            payload={"id": job.external_id}, requester="music_producer",
        )
        if result.get("ok"):
            data = result.get("data") or {}
            mstatus = (data.get("status") or "").lower()
            # Mureka 상태: preparing / queued / running / streaming → 진행 중
            #              succeeded → 완료, failed / timeouted / cancelled → 실패
            if mstatus == "succeeded":
                job.status = "done"
                job.output = data
                _set_agent_status(db, "music_producer", "대기")
                _audit(db, "music.generate.done", target=f"job:{job.id}")
            elif mstatus in {"failed", "error", "timeouted", "cancelled"}:
                job.status = "failed"
                job.error = str(data.get("failed_reason") or data.get("error") or data.get("message") or f"mureka {mstatus or '실패'}")
                _set_agent_status(db, "music_producer", "대기")
                _audit(db, "music.generate.failed", target=f"job:{job.id}", detail={"error": job.error})
            else:
                # 아직 진행 중 (preparing/queued/running/streaming): 출력만 갱신
                job.output = data
        db.commit()

    return _serialize_job(job)


def _serialize_job(j: Job) -> dict:
    out = j.output or {}
    # Mureka는 보통 2개 버전을 choices[]로 돌려준다 (mp3 url + flac_url, ~30일 유효)
    choices = out.get("choices") if isinstance(out, dict) else None
    audio_urls: list[dict] = []
    if isinstance(choices, list):
        for i, c in enumerate(choices):
            if not isinstance(c, dict):
                continue
            url = c.get("url") or c.get("audio_url")
            flac = c.get("flac_url")
            if url or flac:
                audio_urls.append({
                    "index": i,
                    "url": url,
                    "flac_url": flac,
                    "duration_ms": c.get("duration"),
                })
    # 첫 번째 url (구 호환)
    audio_url = audio_urls[0]["url"] if audio_urls else None
    if not audio_url and isinstance(out, dict):
        # 구버전/다른 형식 폴백
        audio_url = (
            out.get("audio_url")
            or out.get("url")
            or (out.get("song") or {}).get("audio_url")
        )
    return {
        "id": j.id,
        "kind": j.kind,
        "department": j.department_slug,
        "status": j.status,
        "external_id": j.external_id,
        "input": j.input or {},
        "audio_url": audio_url,
        "audio_urls": audio_urls,
        "output": out if isinstance(out, dict) else {},
        "error": j.error or "",
        "batch_id": getattr(j, "batch_id", None),
        "removed_at": j.removed_at.isoformat() if getattr(j, "removed_at", None) else None,
        "removed_by": getattr(j, "removed_by", "") or "",
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
    }


# ─── 일일 배치 ───────────────────────────────────────────
def _serialize_batch(b: Batch, jobs: list[Job] | None = None) -> dict:
    out = {
        "id": b.id,
        "run_date": b.run_date,
        "status": b.status,
        "target_count": b.target_count,
        "curated_themes": b.curated_themes or {},
        "deadline_at": b.deadline_at.isoformat() if b.deadline_at else None,
        "youtube_video_id": b.youtube_video_id or "",
        "youtube_url": b.youtube_url or "",
        "image_url": b.image_url or "",
        "error": b.error or "",
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }
    if jobs is not None:
        out["jobs"] = [_serialize_job(j) for j in jobs]
        out["counts"] = {
            "total": len(jobs),
            "done": sum(1 for j in jobs if j.status == "done"),
            "failed": sum(1 for j in jobs if j.status == "failed"),
            "running": sum(1 for j in jobs if j.status == "running"),
            "pending": sum(1 for j in jobs if j.status == "pending"),
            "removed": sum(1 for j in jobs if j.removed_at is not None),
        }
    return out


@router.get("/batches")
def list_batches(limit: int = 14, db: Session = Depends(get_db)):
    """최근 배치 목록 (기본 14일)."""
    rows = (
        db.query(Batch)
        .filter(Batch.department_slug == "music")
        .order_by(desc(Batch.created_at))
        .limit(min(limit, 60))
        .all()
    )
    return [_serialize_batch(b) for b in rows]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    b = db.get(Batch, batch_id)
    if not b:
        raise HTTPException(404, "batch not found")
    jobs = (
        db.query(Job)
        .filter(Job.batch_id == batch_id)
        .order_by(Job.id.asc())
        .all()
    )
    return _serialize_batch(b, jobs=jobs)


@router.get("/batches/today/current")
def get_today_batch(db: Session = Depends(get_db)):
    """오늘의 배치(가장 최근). 없으면 가장 최근 배치를 반환 (검토 중 카드를 놓치지 않도록)."""
    try:
        today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    except Exception:
        today = datetime.now().strftime("%Y-%m-%d")
    b = (
        db.query(Batch)
        .filter(Batch.department_slug == "music", Batch.run_date == today)
        .order_by(desc(Batch.id))
        .first()
    )
    if not b:
        # 가장 최근 배치(아직 검토 중일 수 있음)도 같이 보여주기 좋음
        b = (
            db.query(Batch)
            .filter(Batch.department_slug == "music")
            .order_by(desc(Batch.id))
            .first()
        )
    if not b:
        return None
    jobs = (
        db.query(Job)
        .filter(Job.batch_id == b.id)
        .order_by(Job.id.asc())
        .all()
    )
    return _serialize_batch(b, jobs=jobs)


@router.post("/batches/run-now")
async def run_batch_now():
    """수동 트리거. 스케줄러를 기다리지 않고 즉시 1회 실행.
    오래 걸리는 작업이라 동기 응답은 시작 결과만 줘도 되지만,
    여기서는 단순함을 위해 끝까지 기다린 뒤 요약 반환."""
    result = await run_daily_batch(triggered_by="manual")
    return result


@router.get("/billing-balance")
async def billing_balance(db: Session = Depends(get_db)):
    """잔량 조회 + 추출된 숫자."""
    return await check_mureka_balance(db)


class CuratePreviewRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=20)
    seed_text: str = Field(default="", max_length=2000)


@router.post("/curator/preview")
async def curator_preview(req: CuratePreviewRequest, db: Session = Depends(get_db)):
    """큐레이터 직원 단독 호출. 배치 없이 테마만 미리 본다."""
    return await curator_curate(db, count=req.count, seed_text=req.seed_text)


# ─── 검토 단계: 사이트에서 ❌ ────────────────────────────
class ExcludeRequest(BaseModel):
    by: str = Field(default="owner", max_length=64)


@router.post("/jobs/{job_id}/exclude")
def exclude_job(job_id: int, req: ExcludeRequest, db: Session = Depends(get_db)):
    """곡을 검토 결과에서 제외. 한 명이라도 ❌ 누르면 즉시 제외됨."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.removed_at is not None:
        return _serialize_job(job)  # 이미 제외됨 (idempotent)
    job.removed_at = datetime.utcnow()
    job.removed_by = req.by[:64]
    _audit(db, "music.job.excluded", target=f"job:{job.id}",
           detail={"by": job.removed_by, "batch_id": job.batch_id},
           actor=req.by[:64] or "owner")
    db.commit()
    return _serialize_job(job)


@router.post("/jobs/{job_id}/restore")
def restore_job(job_id: int, db: Session = Depends(get_db)):
    """제외 취소 (실수 복구용)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job.removed_at = None
    job.removed_by = ""
    _audit(db, "music.job.restored", target=f"job:{job.id}", detail={"batch_id": job.batch_id})
    db.commit()
    return _serialize_job(job)
