"""APScheduler 래퍼. agent.schedule_cron이 있으면 자동 실행."""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from backend.db import engine
from backend.models import Agent

scheduler = BackgroundScheduler(timezone=os.environ.get("TZ", "Asia/Seoul"))


def _run_agent_job(agent_id: int) -> None:
    """스케줄러가 호출하는 진입점."""
    # 순환 import 방지를 위해 함수 안에서 import
    from backend.api.agents import run_agent_now

    with Session(engine) as db:
        agent = db.get(Agent, agent_id)
        if not agent or agent.status != "active":
            return
        run_agent_now(agent, db, trigger="schedule")


def schedule_agent(agent: Agent) -> None:
    """직원 한 명 스케줄을 등록/갱신/제거."""
    job_id = f"agent-{agent.id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    if agent.schedule_cron and agent.status == "active":
        try:
            trigger = CronTrigger.from_crontab(agent.schedule_cron)
        except Exception:
            return  # 잘못된 cron 표현은 무시
        scheduler.add_job(
            _run_agent_job,
            trigger,
            args=[agent.id],
            id=job_id,
            replace_existing=True,
        )


def reload_all() -> None:
    """부팅 시 DB의 모든 active agent를 스케줄에 등록."""
    with Session(engine) as db:
        for a in db.exec(select(Agent)).all():
            schedule_agent(a)


def start() -> None:
    if not scheduler.running:
        scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
