"""
Lucky HQ 스케줄러

매일 아침 8시 KST에 일일 큐레이터 안 발송.

APScheduler AsyncIOScheduler로 본부 서버 안에서 직접 실행.
Railway가 24/7 떠있으니 OK.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import SessionLocal
from .daily_curator import make_and_send_today_proposal

log = logging.getLogger("lucky_hq.scheduler")


# 매일 아침 8시 KST
DAILY_CURATOR_HOUR = int(os.environ.get("LUCKY_HQ_CURATOR_HOUR", "8"))
DAILY_CURATOR_MINUTE = int(os.environ.get("LUCKY_HQ_CURATOR_MINUTE", "0"))


async def _run_daily_curator():
    """스케줄러 콜백: 매일 8시 KST에 호출됨."""
    log.info(f"daily curator triggered at {datetime.now()}")
    db = SessionLocal()
    try:
        result = await make_and_send_today_proposal(db)
        log.info(f"daily curator result: {result}")
    except Exception as e:
        log.exception(f"daily curator failed: {e}")
    finally:
        db.close()


def make_scheduler() -> AsyncIOScheduler:
    """스케줄러 인스턴스 생성. main.py에서 lifespan 시작 시 start() 호출."""
    sched = AsyncIOScheduler(timezone="Asia/Seoul")
    sched.add_job(
        _run_daily_curator,
        trigger=CronTrigger(hour=DAILY_CURATOR_HOUR, minute=DAILY_CURATOR_MINUTE,
                            timezone="Asia/Seoul"),
        id="daily_curator_8am",
        name=f"매일 {DAILY_CURATOR_HOUR:02d}:{DAILY_CURATOR_MINUTE:02d} KST 큐레이터 안 발송",
        replace_existing=True,
        misfire_grace_time=3600,  # 1시간 안에 미실행 발견 시 보충 실행
    )
    return sched
