"""
메인 콘솔 API
- GET /api/agents     : 직원 12명 + 현재 상태
- GET /api/departments: 부서 목록
- GET /api/logs       : 최근 감사 로그 (실시간 우측 패널용)
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import Agent, Department, AuditLog, Job

router = APIRouter(prefix="/api", tags=["console"])


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).filter(Agent.is_active == True).all()
    return [
        {
            "id": a.id,
            "slug": a.slug,
            "name": a.name,
            "role": a.role,
            "avatar": a.avatar,
            "voice": a.voice,
            "current_status": a.current_status,
            "department_id": a.department_id,
        }
        for a in rows
    ]


@router.get("/departments")
def list_departments(db: Session = Depends(get_db)):
    rows = db.query(Department).all()
    return [
        {
            "id": d.id,
            "slug": d.slug,
            "name": d.name,
            "status": d.status,
            "description": d.description,
        }
        for d in rows
    ]


@router.get("/logs")
def recent_logs(limit: int = 50, db: Session = Depends(get_db)):
    rows = (
        db.query(AuditLog)
        .order_by(desc(AuditLog.created_at))
        .limit(min(limit, 200))
        .all()
    )
    return [
        {
            "id": l.id,
            "actor": l.actor,
            "action": l.action,
            "target": l.target,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in rows
    ]


@router.get("/jobs/recent")
def recent_jobs(limit: int = 10, db: Session = Depends(get_db)):
    rows = db.query(Job).order_by(desc(Job.created_at)).limit(min(limit, 50)).all()
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "department": j.department_slug,
            "status": j.status,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in rows
    ]
