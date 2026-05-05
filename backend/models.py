"""DB 모델. 메모리에 정의된 회사/멤버/부서/직원/디렉티브/Job/감사로그 골격."""
from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, JSON


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(unique=True, index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: str
    role: str = "owner"  # owner|manager|operator|approver|viewer|guest
    telegram_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Department(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    slug: str = Field(index=True)
    name: str
    status: str = "active"  # proposed|piloting|active|paused
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Agent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    department_id: Optional[int] = Field(default=None, foreign_key="department.id")
    slug: str = Field(index=True)
    name: str
    module: str = Field(index=True)  # 플러그인 슬러그 (예: 'issue_scout')
    role_prompt: str = ""
    voice: str = ""
    llm_tier: str = "T1"  # T0|T1|T2
    schedule_cron: Optional[str] = None  # 예: "*/30 * * * *"
    status: str = "active"  # active|paused|archived
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Directive(SQLModel, table=True):
    """텔레그램/콘솔 한 줄로 사이트/부서/직원 운영 방향을 영구 변경."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    scope: str = "global"  # global|department|agent|site
    target_id: Optional[int] = None
    text: str
    enforcement: str = "soft"  # hint|soft|hard|blocking
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    agent_id: int = Field(foreign_key="agent.id", index=True)
    status: str = "queued"  # queued|running|done|error|cancelled
    trigger: str = "manual"  # manual|schedule|directive|telegram
    input: dict = Field(default_factory=dict, sa_column=Column(JSON))
    output: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    actor: str  # user:1, agent:3, system, scheduler
    action: str
    target: str  # type:id (예: agent:5)
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
