"""
배치 워커

전체 흐름:
  1. 큐레이터 → 이슈 N개 픽
  2. 각 이슈마다:
     a. 작곡가가 기획안 생성 (LLM 또는 룰)
     b. 음악 프로듀서가 Mureka에 의뢰
     c. Mureka 폴링 (작업 완료까지)
     d. 텔레그램으로 곡 audio 전송
  3. 배치 완료 보고

순차 처리 (Mureka rate limit 회피).
백그라운드 태스크로 실행 — fastapi BackgroundTasks 또는 asyncio.create_task.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Batch, Job, AuditLog, Agent
from .api_manager import call_api
from .songwriter import compose_plan as songwriter_compose
from . import telegram_agent

log = logging.getLogger("lucky_hq.batch")

# Mureka 폴링 설정
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 240   # 1곡당 최대 4분 대기
INTER_SONG_DELAY_SEC = 3  # 곡과 곡 사이 간격 (rate limit 안전)


def _audit(db: Session, action: str, target: str = "", detail: dict | None = None,
           actor: str = "music_producer") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail or {}))


def _set_agent(db: Session, slug: str, status: str) -> None:
    a = db.query(Agent).filter_by(slug=slug).first()
    if a:
        a.current_status = status
        a.last_seen_at = datetime.utcnow()


async def run_batch(batch_id: int) -> None:
    """배치 1건 끝까지 처리. 자체 DB 세션 사용 (BackgroundTask이므로)."""
    db = SessionLocal()
    try:
        batch = db.get(Batch, batch_id)
        if not batch:
            log.error(f"batch {batch_id} not found")
            return

        batch.status = "running"
        batch.started_at = datetime.utcnow()
        _audit(db, "batch.started", target=f"batch:{batch_id}",
               detail={"target_count": batch.target_count, "trigger": batch.trigger})
        db.commit()

        issues: list[str] = list(batch.issues or [])

        for idx, issue in enumerate(issues, start=1):
            try:
                await _process_one(db, batch, issue, idx)
            except Exception as e:
                log.exception(f"batch {batch_id} song {idx} failed")
                _audit(db, "batch.song_error", target=f"batch:{batch_id}",
                       detail={"idx": idx, "error": str(e)[:200]})
                db.commit()

            # 다음 곡 전 잠시 휴식
            if idx < len(issues):
                await asyncio.sleep(INTER_SONG_DELAY_SEC)

        # 완료 처리
        db.refresh(batch)
        completed = db.query(Job).filter_by(batch_id=batch_id, status="done").count()
        failed = db.query(Job).filter_by(batch_id=batch_id, status="failed").count()
        batch.completed_count = completed
        batch.failed_count = failed
        batch.status = "reporting"
        batch.finished_at = datetime.utcnow()
        _audit(db, "batch.compute_done", target=f"batch:{batch_id}",
               detail={"completed": completed, "failed": failed})
        db.commit()

        # 텔레그램 보고
        _set_agent(db, "telegram", "보고 중")
        db.commit()
        try:
            await telegram_agent.report_batch(db, batch)
            db.commit()

            # 성공한 곡들 audio 전송
            done_jobs = db.query(Job).filter_by(batch_id=batch_id, status="done").order_by(Job.id).all()
            for job in done_jobs:
                try:
                    await telegram_agent.send_audio_for_job(db, job)
                    db.commit()
                except Exception as e:
                    log.exception(f"telegram audio send failed for job {job.id}")
                    _audit(db, "telegram.send_error", target=f"job:{job.id}",
                           detail={"error": str(e)[:200]}, actor="telegram")
                    db.commit()
                # 텔레그램도 너무 빠르게 보내면 안 됨
                await asyncio.sleep(1.5)
        except Exception as e:
            log.exception("telegram report failed")
            _audit(db, "telegram.report_error", target=f"batch:{batch_id}",
                   detail={"error": str(e)[:200]}, actor="telegram")
            db.commit()

        _set_agent(db, "telegram", "대기")
        batch.status = "done"
        _audit(db, "batch.finished", target=f"batch:{batch_id}",
               detail={"completed": completed, "failed": failed})
        db.commit()

    except Exception as e:
        log.exception(f"run_batch {batch_id} crashed")
        try:
            batch = db.get(Batch, batch_id)
            if batch:
                batch.status = "failed"
                batch.error = str(e)[:500]
                _audit(db, "batch.crashed", target=f"batch:{batch_id}",
                       detail={"error": str(e)[:200]})
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def _process_one(db: Session, batch: Batch, issue: str, idx: int) -> None:
    """단일 이슈 → 작곡가 → Mureka → 폴링까지 처리."""
    # 1) 작곡가 기획
    _set_agent(db, "songwriter", f"#{idx}/{len(batch.issues or [])} 기획 중")
    db.commit()

    plan = await songwriter_compose(issue, db=db)

    # 2) Job 생성 (배치 연결)
    job = Job(
        kind="music_generate",
        department_slug="music",
        agent_slug="music_producer",
        status="pending",
        batch_id=batch.id,
        input={
            "issue": issue,
            "title": plan["title"],
            "style": plan["style"],
            "lyrics_len": len(plan["lyrics"]),
            "mood": plan.get("mood"),
            "source": plan.get("source"),
        },
    )
    db.add(job)
    db.flush()
    _audit(db, "music.song_planned", target=f"job:{job.id}",
           detail={"title": plan["title"], "mood": plan.get("mood"), "source": plan.get("source")},
           actor="songwriter")
    db.commit()

    # 3) Mureka 호출
    _set_agent(db, "music_producer", f"#{idx} Mureka 호출 중")
    db.commit()

    payload = {"lyrics": plan["lyrics"], "style": plan["style"], "title": plan["title"]}
    result = await call_api(db, provider="mureka", operation="generate",
                            payload=payload, requester="music_producer")

    job = db.get(Job, job.id)
    if not result.get("ok"):
        job.status = "failed"
        job.error = result.get("error", "Mureka generate 실패")[:500]
        _audit(db, "music.generate_failed", target=f"job:{job.id}",
               detail={"error": job.error[:120]})
        db.commit()
        return

    data = result.get("data") or {}
    ext_id = data.get("id") or data.get("task_id") or data.get("song_id")
    if not ext_id:
        job.status = "failed"
        job.error = f"Mureka 응답에 id 없음: {str(data)[:200]}"
        _audit(db, "music.generate_failed", target=f"job:{job.id}", detail={"reason": "no_id"})
        db.commit()
        return

    job.external_id = str(ext_id)
    job.status = "running"
    job.output = {"submitted": data}
    _audit(db, "music.generate_submitted", target=f"job:{job.id}",
           detail={"external_id": ext_id})
    db.commit()

    # 4) 폴링
    _set_agent(db, "music_producer", f"#{idx} 결과 대기")
    db.commit()

    deadline = datetime.utcnow() + timedelta(seconds=POLL_TIMEOUT_SEC)
    while datetime.utcnow() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        q = await call_api(db, provider="mureka", operation="query",
                           payload={"id": ext_id}, requester="music_producer")
        if not q.get("ok"):
            # 일시적 에러일 수 있으니 deadline까지 계속 시도
            continue

        qdata = q.get("data") or {}
        mstatus = (qdata.get("status") or "").lower()

        # 다양한 응답 형태에서 audio_url 후보 추출
        audio_url = (
            qdata.get("audio_url")
            or qdata.get("url")
            or (qdata.get("song") or {}).get("audio_url")
            or (qdata.get("data") or {}).get("audio_url")
        )
        # 응답에 choices/songs 같은 배열이 있을 경우 첫 항목
        if not audio_url:
            for k in ("choices", "songs", "results"):
                arr = qdata.get(k)
                if isinstance(arr, list) and arr:
                    item = arr[0]
                    if isinstance(item, dict):
                        audio_url = item.get("audio_url") or item.get("url") or item.get("audio")
                        if audio_url:
                            break

        job = db.get(Job, job.id)
        job.output = qdata

        if mstatus in {"succeeded", "success", "done", "completed", "finished"} or audio_url:
            if audio_url:
                # 출력에 audio_url을 한 번 더 평탄화해서 저장
                job.output = {**qdata, "audio_url": audio_url}
            job.status = "done"
            _audit(db, "music.generate_done", target=f"job:{job.id}",
                   detail={"has_audio": bool(audio_url)})
            db.commit()
            return

        if mstatus in {"failed", "error", "rejected"}:
            job.status = "failed"
            job.error = str(qdata.get("error") or qdata.get("message") or "mureka 실패")[:500]
            _audit(db, "music.generate_failed", target=f"job:{job.id}",
                   detail={"error": job.error[:120]})
            db.commit()
            return

        # 진행 중이면 계속
        db.commit()

    # 타임아웃
    job = db.get(Job, job.id)
    job.status = "failed"
    job.error = f"폴링 타임아웃 ({POLL_TIMEOUT_SEC}s)"
    _audit(db, "music.generate_timeout", target=f"job:{job.id}")
    db.commit()