"""
곡 보관 직원 보조 모듈

Mureka audio_url을 다운로드해서 로컬에 저장.
파일은 ARCHIVE_DIR (Railway 볼륨 또는 임시 디스크)에 저장.
사이트의 /audio/{job_id}로 서빙.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from .models import Job, AuditLog

log = logging.getLogger("lucky_hq.archive")

# 곡 보관 디렉토리
# Railway에서 볼륨 마운트 안 했으면 /tmp 사용 (재배포 시 사라짐 — 알림용)
ARCHIVE_DIR = Path(
    os.environ.get("LUCKY_HQ_ARCHIVE_DIR", "/tmp/lucky_hq_archive")
)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _ext_from_url(url: str) -> str:
    """URL에서 확장자 추출 (mp3, wav, m4a 등). 없으면 mp3."""
    path = url.split("?")[0].lower()
    for ext in ("mp3", "wav", "m4a", "ogg", "flac"):
        if path.endswith(f".{ext}"):
            return ext
    return "mp3"


async def download_and_archive(db: Session, job: Job) -> dict:
    """곡 1개의 audio_url을 다운로드해서 로컬에 저장.

    Returns: {"ok": bool, "path": str, "size": int, "error": str}
    """
    audio_url = (job.output or {}).get("audio_url")
    if not audio_url:
        return {"ok": False, "path": "", "size": 0, "error": "audio_url 없음"}

    ext = _ext_from_url(audio_url)
    out_path = ARCHIVE_DIR / f"job_{job.id}.{ext}"

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", audio_url) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
    except Exception as e:
        log.exception(f"audio download failed for job {job.id}")
        return {"ok": False, "path": "", "size": 0, "error": str(e)[:300]}

    size = out_path.stat().st_size
    job.local_audio_path = str(out_path)
    job.local_audio_size = size
    job.archived_at = datetime.utcnow()

    db.add(AuditLog(
        actor="archiver",
        action="audio.archived",
        target=f"job:{job.id}",
        detail={"size_kb": size // 1024, "ext": ext},
    ))
    db.commit()
    return {"ok": True, "path": str(out_path), "size": size, "error": ""}


def delete_archived(db: Session, job: Job) -> bool:
    """로컬 파일 삭제 + Job soft-delete."""
    if not job.local_audio_path:
        return False
    p = Path(job.local_audio_path)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    job.local_audio_path = ""
    job.local_audio_size = 0
    job.deleted_at = datetime.utcnow()
    db.add(AuditLog(
        actor="owner",
        action="audio.deleted",
        target=f"job:{job.id}",
    ))
    db.commit()
    return True


def archived_path_for(job: Job) -> Path | None:
    if not job.local_audio_path:
        return None
    p = Path(job.local_audio_path)
    return p if p.exists() else None
