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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import Job, Agent, AuditLog
from ..api_manager import call_api
from ..songwriter import compose_plan as songwriter_compose
from .. import archiver

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
    payload = {"lyrics": req.lyrics, "style": req.style, "title": req.title}
    result = await call_api(
        db, provider="mureka", operation="generate",
        payload=payload, requester="music_producer",
    )

    # 3) 결과 반영
    job = db.get(Job, job.id)
    if result.get("ok"):
        data = result.get("data") or {}
        # Mureka 응답 형태가 확정되지 않았으니 후보 키들을 모두 탐색
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
            # Mureka 상태 매핑 (확정 전: 가능한 후보 다수 처리)
            if mstatus in {"succeeded", "success", "done", "completed"}:
                job.status = "done"
                job.output = data
                _set_agent_status(db, "music_producer", "대기")
                _audit(db, "music.generate.done", target=f"job:{job.id}")
            elif mstatus in {"failed", "error"}:
                job.status = "failed"
                job.error = str(data.get("error") or data.get("message") or "mureka 실패")
                _set_agent_status(db, "music_producer", "대기")
                _audit(db, "music.generate.failed", target=f"job:{job.id}", detail={"error": job.error})
            else:
                # 아직 진행 중: 출력만 갱신
                job.output = data
        db.commit()

    return _serialize_job(job)


def _serialize_job(j: Job) -> dict:
    out = j.output or {}
    # 결과 오디오 URL 추출 (Mureka 응답 후보 키)
    audio_url = (
        out.get("audio_url")
        or out.get("url")
        or (out.get("song") or {}).get("audio_url")
        or (out.get("data") or {}).get("audio_url")
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


# ── 곡 보관 (다운로드 + 사이트 재생 + 삭제) ─────────────────
@router.get("/archive")
def list_archived(limit: int = 50, db: Session = Depends(get_db)):
    """보관된 곡 목록 (최신순)."""
    rows = (
        db.query(Job)
        .filter(Job.local_audio_path != "", Job.deleted_at.is_(None))
        .order_by(desc(Job.archived_at))
        .limit(min(limit, 200))
        .all()
    )
    return [
        {
            "id": j.id,
            "title": (j.input or {}).get("title", f"#{j.id}"),
            "issue": (j.input or {}).get("issue", ""),
            "style": (j.input or {}).get("style", ""),
            "mood": (j.input or {}).get("mood", ""),
            "review_status": j.review_status,
            "size_kb": j.local_audio_size // 1024 if j.local_audio_size else 0,
            "audio_url": f"/api/music/audio/{j.id}",
            "archived_at": j.archived_at.isoformat() if j.archived_at else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in rows
    ]


@router.get("/audio/{job_id}")
def serve_audio(job_id: int, db: Session = Depends(get_db)):
    """보관된 audio 파일 서빙 (브라우저 재생용)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "곡을 찾을 수 없음")
    path = archiver.archived_path_for(job)
    if not path:
        raise HTTPException(404, "보관 파일 없음 (재생성 필요)")
    media_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=f"{job.id}_{path.name}")


@router.post("/archive/{job_id}")
async def archive_one(job_id: int, db: Session = Depends(get_db)):
    """이미 만들어진 곡을 수동으로 보관 (다운로드 재시도)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "곡을 찾을 수 없음")
    if job.status != "done":
        raise HTTPException(400, f"곡 상태가 done이 아님: {job.status}")
    result = await archiver.download_and_archive(db, job)
    return result


@router.delete("/archive/{job_id}")
def delete_archived(job_id: int, db: Session = Depends(get_db)):
    """보관된 곡 삭제 (파일 + DB soft-delete)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "곡을 찾을 수 없음")
    archiver.delete_archived(db, job)
    return {"ok": True}


# ── 큐레이터 5×3 안 ──────────────────────────────────────
@router.get("/curator/options")
async def curator_options(db: Session = Depends(get_db)):
    """큐레이터가 오늘의 5x3 안 (언어/분위기/키워드) 제안.
    Gemini로 오늘 트렌드/계절 반영. 실패 시 폴백."""
    from ..curator import propose_options
    return await propose_options(db)


# ── 일일 큐레이터 수동 트리거 ────────────────────────────
@router.post("/daily/trigger-now")
async def trigger_daily_now(db: Session = Depends(get_db)):
    """매일 8시 자동 외에 수동으로 트리거 (테스트 + 비상시)."""
    from ..daily_curator import make_and_send_today_proposal
    return await make_and_send_today_proposal(db)


@router.get("/daily/today")
def get_today_proposal(db: Session = Depends(get_db)):
    """오늘 발송된 proposal 상태."""
    from ..models import DailyProposal
    from ..daily_curator import _kst_today
    p = (
        db.query(DailyProposal)
        .filter_by(date_kst=_kst_today())
        .order_by(DailyProposal.id.desc())
        .first()
    )
    if not p:
        return {"exists": False}
    return {
        "exists": True,
        "id": p.id,
        "date_kst": p.date_kst,
        "status": p.status,
        "languages": p.languages,
        "moods": p.moods,
        "keywords": p.keywords,
        "chosen": {
            "language_idx": p.chosen_language_idx,
            "mood_idx": p.chosen_mood_idx,
            "keyword_idx": p.chosen_keyword_idx,
            "by_chat_id": p.chosen_by_chat_id,
        } if p.status == "chosen" else None,
        "triggered_batch_id": p.triggered_batch_id,
        "sent_at": p.sent_at.isoformat() if p.sent_at else None,
        "chosen_at": p.chosen_at.isoformat() if p.chosen_at else None,
    }
