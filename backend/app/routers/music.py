"""
음악제작 부서 API

엔드포인트:
- POST /api/music/generate         : 곡 생성 요청 (Job 생성 + Mureka 호출)
- GET  /api/music/jobs             : 부서 작업 목록
- GET  /api/music/jobs/{job_id}    : 단일 작업 상태 (자동 폴링/갱신)

Mureka 호출은 반드시 api_manager.call_api()를 통해서만 진행한다.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import Job, Agent, AuditLog
from ..api_manager import call_api
from ..songwriter import compose_plan as songwriter_compose

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
    # Mureka 응답: choices[0].url 이 mp3, choices[0].flac_url 이 flac (~30일 유효)
    choices = out.get("choices") or []
    audio_url = None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        audio_url = first.get("url") or first.get("flac_url") or first.get("audio_url")
    if not audio_url:
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
        "error": j.error or "",
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
    }
