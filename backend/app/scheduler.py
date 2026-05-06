"""
일일 스케줄러 (APScheduler)

- FastAPI lifespan에 start/stop을 부착한다.
- 매일 settings.batch_schedule_hour:minute (기본 KST 09:00) 에 run_daily_batch.
- 검토 마감 deadline 체크: 매 5분마다 awaiting_review 배치를 훑어 deadline 지나면
  finalize 단계로 넘긴다 (Phase 3에서 ffmpeg+YouTube 연결).

Railway는 단일 web 프로세스(uvicorn)라 in-process 스케줄러로 충분.
다중 replica로 가게 되면 외부 큐(Celery/Redis) 또는 Railway Cron Service로 이전.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .batch_runner import run_daily_batch
from .config import get_settings
from .db import SessionLocal
from .models import AuditLog, Batch

log = logging.getLogger("lucky_hq.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _job_daily_batch():
    log.info("일일 배치 시작 (scheduler trigger)")
    try:
        result = await run_daily_batch(triggered_by="scheduler")
        log.info("일일 배치 끝: %s", result)
    except Exception as e:
        log.exception("일일 배치 예외: %s", e)


async def _job_check_deadlines():
    """awaiting_review 중 deadline 지난 배치를 finalizing으로 전환.
    Phase 3에서 finalize_batch(ffmpeg + YouTube) 호출 추가 예정."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        rows = (
            db.query(Batch)
            .filter(Batch.status == "awaiting_review",
                    Batch.deadline_at != None,    # noqa: E711
                    Batch.deadline_at <= now)
            .all()
        )
        for b in rows:
            b.status = "finalizing"
            db.add(AuditLog(actor="scheduler", action="batch.deadline_passed",
                            target=f"batch:{b.id}",
                            detail={"deadline_at": b.deadline_at.isoformat() if b.deadline_at else None}))
        if rows:
            db.commit()
            log.info("검토 마감 배치 %d개 → finalizing", len(rows))
    except Exception as e:
        db.rollback()
        log.exception("deadline 체크 예외: %s", e)
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    if not settings.batch_enabled:
        log.info("스케줄러 비활성 (batch_enabled=false)")
        return

    sched = AsyncIOScheduler(timezone=settings.batch_timezone or "Asia/Seoul")
    # 매일 곡 생성
    sched.add_job(
        _job_daily_batch,
        trigger=CronTrigger(
            hour=int(settings.batch_schedule_hour or 9),
            minute=int(settings.batch_schedule_minute or 0),
            timezone=settings.batch_timezone or "Asia/Seoul",
        ),
        id="daily_batch",
        replace_existing=True,
        misfire_grace_time=60 * 30,  # 30분 늦게 깨어나도 실행
    )
    # 5분마다 검토 마감 체크
    sched.add_job(
        _job_check_deadlines,
        trigger=IntervalTrigger(minutes=5),
        id="check_deadlines",
        replace_existing=True,
    )
    sched.start()
    _scheduler = sched
    log.info("스케줄러 시작 — daily=%02d:%02d %s, review_min=%d",
             settings.batch_schedule_hour, settings.batch_schedule_minute,
             settings.batch_timezone, settings.batch_review_minutes)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        pass
    _scheduler = None
