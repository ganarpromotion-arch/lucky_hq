"""
배치 라우터

- POST /api/music/batches           : 새 배치 시작 (테스트 버튼)
- GET  /api/music/batches           : 배치 목록
- GET  /api/music/batches/{id}      : 배치 상세 + 곡 목록
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import Batch, Job, AuditLog
from ..curator import pick_issues, today_seed
from ..batch_worker import run_batch

router = APIRouter(prefix="/api/music/batches", tags=["batches"])


class CreateBatchRequest(BaseModel):
    target_count: int = Field(default=6, ge=1, le=10)
    trigger: str = Field(default="test_button")
    use_today_seed: bool = Field(default=False, description="True면 오늘 날짜 기반 결정적 픽")
    make_video: bool = Field(default=False, description="True면 곡마다 mp4 만들어 전송")


@router.post("")
async def create_batch(req: CreateBatchRequest, bg: BackgroundTasks,
                       db: Session = Depends(get_db)):
    # 동시 실행 보호: 진행 중인 배치 있으면 거부
    busy = db.query(Batch).filter(
        Batch.department_slug == "music",
        Batch.status.in_(("pending", "running", "reporting")),
    ).first()
    if busy:
        raise HTTPException(409, f"이미 진행 중인 배치가 있습니다 (#{busy.id}). 완료 후 다시 시도해주세요.")

    seed = today_seed() if req.use_today_seed else None
    issues = pick_issues(n=req.target_count, seed=seed)

    batch = Batch(
        department_slug="music",
        kind=f"music_{req.target_count}{'_v' if req.make_video else ''}",
        trigger=req.trigger,
        status="pending",
        target_count=req.target_count,
        issues=issues,
        make_video=req.make_video,
    )
    db.add(batch)
    db.flush()
    db.add(AuditLog(
        actor="owner" if req.trigger == "test_button" else "scheduler",
        action="batch.created",
        target=f"batch:{batch.id}",
        detail={"trigger": req.trigger, "target_count": req.target_count,
                "make_video": req.make_video, "issues": issues},
    ))
    db.commit()
    db.refresh(batch)

    # 백그라운드로 워커 실행
    bg.add_task(run_batch, batch.id)

    return _serialize(batch, jobs=[])


@router.get("")
def list_batches(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.query(Batch).filter(Batch.department_slug == "music")\
        .order_by(desc(Batch.created_at)).limit(min(limit, 50)).all()
    return [_serialize(b, jobs=None) for b in rows]


@router.get("/{batch_id}")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "batch not found")
    jobs = db.query(Job).filter_by(batch_id=batch_id).order_by(Job.id).all()
    return _serialize(batch, jobs=jobs)


def _serialize(batch: Batch, jobs=None) -> dict:
    out = {
        "id": batch.id,
        "kind": batch.kind,
        "trigger": batch.trigger,
        "status": batch.status,
        "target_count": batch.target_count,
        "completed_count": batch.completed_count,
        "failed_count": batch.failed_count,
        "issues": batch.issues or [],
        "make_video": bool(batch.make_video),
        "error": batch.error or "",
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }
    if jobs is not None:
        out["jobs"] = [_serialize_job(j) for j in jobs]
    return out


def _serialize_job(j: Job) -> dict:
    out = j.output or {}
    audio_url = (
        out.get("audio_url")
        or out.get("url")
        or (out.get("song") or {}).get("audio_url")
        or (out.get("data") or {}).get("audio_url")
    )
    return {
        "id": j.id,
        "title": (j.input or {}).get("title", ""),
        "issue": (j.input or {}).get("issue", ""),
        "style": (j.input or {}).get("style", ""),
        "mood": (j.input or {}).get("mood", ""),
        "status": j.status,
        "review_status": j.review_status,
        "audio_url": audio_url,
        "error": j.error or "",
        "telegram_message_id": j.telegram_message_id,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }
