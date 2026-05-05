"""직원 API. 모듈 카탈로그, CRUD, ▶ 일하기, Job 기록."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.db import get_session
from backend.models import Agent, Job, AuditLog, Company
from backend.agents.registry import get as get_module, all_modules
from backend.core.scheduler import schedule_agent

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ─────────────────────────────────────────────────────────────────────────
# 스키마
# ─────────────────────────────────────────────────────────────────────────
class AgentCreate(BaseModel):
    name: str
    module: str
    slug: Optional[str] = None
    department_id: Optional[int] = None
    role_prompt: Optional[str] = None
    voice: Optional[str] = ""
    llm_tier: Optional[str] = None
    schedule_cron: Optional[str] = None
    config: Optional[dict] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    role_prompt: Optional[str] = None
    voice: Optional[str] = None
    llm_tier: Optional[str] = None
    schedule_cron: Optional[str] = None
    status: Optional[str] = None
    config: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────
# 모듈 카탈로그 (마법사가 사용)
# ─────────────────────────────────────────────────────────────────────────
@router.get("/modules")
def list_modules() -> list[dict]:
    return [
        {
            "slug": m.slug,
            "label": m.label,
            "description": m.description,
            "config_schema": m.config_schema,
            "default_llm_tier": m.default_llm_tier,
            "default_role_prompt": m.default_role_prompt,
            "default_schedule_cron": m.default_schedule_cron,
        }
        for m in all_modules()
    ]


# ─────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────
def _ensure_company(db: Session) -> Company:
    """V1: 회사 1개 자동 생성. V2에서 멀티 회사 지원."""
    company = db.exec(select(Company)).first()
    if not company:
        company = Company(slug="lucky", name="Lucky Company")
        db.add(company)
        db.commit()
        db.refresh(company)
    return company


@router.get("")
def list_agents(db: Session = Depends(get_session)):
    return db.exec(
        select(Agent).where(Agent.status != "archived").order_by(Agent.id)
    ).all()


@router.post("")
def create_agent(payload: AgentCreate, db: Session = Depends(get_session)):
    mod = get_module(payload.module)
    if not mod:
        raise HTTPException(400, f"unknown module: {payload.module}")

    company = _ensure_company(db)
    agent = Agent(
        company_id=company.id,
        department_id=payload.department_id,
        slug=payload.slug or f"{payload.module}-{int(datetime.utcnow().timestamp())}",
        name=payload.name,
        module=payload.module,
        role_prompt=payload.role_prompt or mod.default_role_prompt,
        voice=payload.voice or "",
        llm_tier=payload.llm_tier or mod.default_llm_tier,
        schedule_cron=payload.schedule_cron or (mod.default_schedule_cron or None),
        config=payload.config or {},
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    db.add(
        AuditLog(
            company_id=company.id,
            actor="user:?",
            action="agent.create",
            target=f"agent:{agent.id}",
            detail={"module": payload.module, "name": agent.name},
        )
    )
    db.commit()

    schedule_agent(agent)
    return agent


@router.patch("/{agent_id}")
def update_agent(agent_id: int, payload: AgentUpdate, db: Session = Depends(get_session)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(agent, k, v)
    db.add(agent)
    db.commit()
    db.refresh(agent)
    schedule_agent(agent)
    return agent


@router.delete("/{agent_id}")
def archive_agent(agent_id: int, db: Session = Depends(get_session)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404)
    agent.status = "archived"
    db.add(agent)
    db.commit()
    schedule_agent(agent)  # 스케줄 제거
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────
# ▶ 일하기 (수동) + 스케줄러도 호출하는 공용 함수
# ─────────────────────────────────────────────────────────────────────────
class RunRequest(BaseModel):
    ctx: Optional[dict] = None


@router.post("/{agent_id}/run")
def run_endpoint(
    agent_id: int,
    body: Optional[RunRequest] = None,
    db: Session = Depends(get_session),
):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404)
    if agent.status != "active":
        raise HTTPException(400, f"agent status={agent.status}")
    ctx = (body.ctx if body else None) or {}
    job = run_agent_now(agent, db, trigger="manual", ctx=ctx)
    return job


def run_agent_now(
    agent: Agent,
    db: Session,
    trigger: str = "manual",
    ctx: dict | None = None,
) -> Job:
    """모듈을 찾고 실행하고 Job에 결과 기록. 스케줄러도 동일 함수 사용."""
    mod_cls = get_module(agent.module)
    if not mod_cls:
        job = Job(
            company_id=agent.company_id,
            agent_id=agent.id,
            status="error",
            trigger=trigger,
            error=f"module {agent.module} not found",
            started_at=datetime.utcnow(),
            finished_at=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    job = Job(
        company_id=agent.company_id,
        agent_id=agent.id,
        status="running",
        trigger=trigger,
        started_at=datetime.utcnow(),
        input=ctx or {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        result = mod_cls().do_work(agent, db, ctx or {})
        if not isinstance(result, dict):
            result = {"result": str(result)}
        job.output = result
        job.status = "done"
    except Exception as e:
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"

    job.finished_at = datetime.utcnow()
    db.add(job)

    db.add(
        AuditLog(
            company_id=agent.company_id,
            actor=f"agent:{agent.id}",
            action=f"job.{job.status}",
            target=f"job:{job.id}",
            detail={"trigger": trigger},
        )
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/{agent_id}/jobs")
def list_jobs(agent_id: int, limit: int = 20, db: Session = Depends(get_session)):
    return db.exec(
        select(Job)
        .where(Job.agent_id == agent_id)
        .order_by(Job.id.desc())
        .limit(limit)
    ).all()
