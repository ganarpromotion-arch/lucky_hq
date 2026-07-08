"""
롱폼 수면·앰비언트 영상 API

- POST /api/music/longform            : 보관곡 1개 → 1시간 루프 영상 렌더 큐잉
- GET  /api/music/longform            : 릴리스 목록 (갤러리)
- GET  /api/music/longform/{id}       : 단건 상태 + 영어 메타데이터
- GET  /api/music/longform-file/{id}  : mp4 다운로드 (유튜브 수동 업로드용)
- GET  /api/music/longform-cover/{id} : 커버 PNG
"""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LongformRelease, Job, AuditLog
from ..longform import render_release, build_metadata

router = APIRouter(prefix="/api/music", tags=["longform"])


class LongformRequest(BaseModel):
    # job_id(단일) 또는 job_ids(컴필레이션) 중 하나. 둘 다 오면 job_ids 우선.
    job_id: int | None = None
    job_ids: list[int] = Field(default_factory=list, max_length=30)
    target_min: int = Field(default=60, ge=10, le=180)   # 10~180분
    niche: str = Field(default="sleep", pattern="^(sleep|study|cinematic)$")


def _serialize(rel: LongformRelease) -> dict:
    return {
        "id": rel.id,
        "job_id": rel.job_id,
        "track_count": len(rel.source_job_ids or [rel.job_id]),
        "theme": rel.theme,
        "niche": rel.niche,
        "status": rel.status,
        "target_sec": rel.target_sec,
        "duration_sec": rel.duration_sec or 0,
        "size_mb": (rel.file_size // 1024 // 1024) if rel.file_size else 0,
        "cover_url": f"/api/music/longform-cover/{rel.id}" if rel.cover_path else "",
        "download_url": f"/api/music/longform-file/{rel.id}" if rel.status == "done" else "",
        "yt_title": rel.yt_title or "",
        "yt_description": rel.yt_description or "",
        "yt_tags": rel.yt_tags or [],
        "error": rel.error or "",
        "created_at": rel.created_at.isoformat() if rel.created_at else None,
    }


@router.post("/longform")
def create_longform(req: LongformRequest, bg: BackgroundTasks, db: Session = Depends(get_db)):
    """보관곡 1개(루프) 또는 여러 곡(컴필레이션)을 목표 길이의 롱폼 영상으로 렌더 (백그라운드)."""
    # 소스 곡 목록 결정 (job_ids 우선)
    ids = req.job_ids or ([req.job_id] if req.job_id is not None else [])
    if not ids:
        raise HTTPException(400, "job_id 또는 job_ids 중 하나는 필요")

    # 모든 소스가 보관된 완성곡인지 검증
    jobs = []
    for jid in ids:
        j = db.get(Job, jid)
        if not j or j.deleted_at is not None:
            raise HTTPException(404, f"곡 #{jid} 없음")
        if j.status != "done" or not j.local_audio_path:
            raise HTTPException(400, f"곡 #{jid} 은 보관된 완성곡이 아님")
        jobs.append(j)

    lead = jobs[0]  # 대표 곡 — 제목/주제/메타데이터 기준
    theme = (lead.input or {}).get("issue", "") or (lead.input or {}).get("title", "")
    title_hint = (lead.input or {}).get("title", "")
    target_sec = req.target_min * 60
    from ..config import get_settings
    s = get_settings()
    meta = build_metadata(theme=theme, niche=req.niche,
                          target_min=req.target_min, title_hint=title_hint,
                          channel_name=s.channel_name, channel_handle=s.channel_handle)

    rel = LongformRelease(
        job_id=lead.id, source_job_ids=ids, theme=theme[:300], niche=req.niche,
        target_sec=target_sec, status="pending",
        yt_title=meta["yt_title"], yt_description=meta["yt_description"],
        yt_tags=meta["yt_tags"],
    )
    db.add(rel)
    db.add(AuditLog(actor="owner", action="longform.requested",
                    target=f"job:{lead.id}",
                    detail={"target_min": req.target_min, "niche": req.niche,
                            "tracks": len(ids), "source_job_ids": ids}))
    db.commit()
    db.refresh(rel)

    bg.add_task(render_release, rel.id)
    return _serialize(rel)


@router.get("/longform")
def list_longform(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(LongformRelease)
        .order_by(desc(LongformRelease.created_at))
        .limit(min(limit, 200))
        .all()
    )
    return [_serialize(r) for r in rows]


@router.get("/longform/{rel_id}")
def get_longform(rel_id: int, db: Session = Depends(get_db)):
    rel = db.get(LongformRelease, rel_id)
    if not rel:
        raise HTTPException(404, "릴리스 없음")
    return _serialize(rel)


@router.get("/longform-file/{rel_id}")
def serve_longform(rel_id: int, db: Session = Depends(get_db)):
    rel = db.get(LongformRelease, rel_id)
    if not rel or rel.status != "done" or not rel.video_path:
        raise HTTPException(404, "영상 없음 (아직 렌더 중이거나 실패)")
    p = Path(rel.video_path)
    if not p.exists():
        raise HTTPException(404, "영상 파일이 디스크에 없음")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (rel.yt_title or f"longform_{rel_id}"))[:80]
    return FileResponse(p, media_type="video/mp4", filename=f"{safe}.mp4")


@router.get("/longform-cover/{rel_id}")
def serve_longform_cover(rel_id: int, db: Session = Depends(get_db)):
    rel = db.get(LongformRelease, rel_id)
    if not rel or not rel.cover_path:
        raise HTTPException(404, "커버 없음")
    p = Path(rel.cover_path)
    if not p.exists():
        raise HTTPException(404, "커버 파일이 디스크에 없음")
    return FileResponse(p, media_type="image/png", filename=f"cover_{rel_id}.png")
