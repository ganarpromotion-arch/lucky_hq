"""
Lucky HQ 데이터 모델 (V1)

핵심 개념:
- Department: 부서 (status로 단계 운영: proposed → piloting → active)
- Agent: 직원 (역할/voice/system_prompt를 갖는 AI 페르소나)
- Job: 작업 단위 (음악 생성 등 비동기 외부 호출 추적)
- AuditLog: 모든 행동 기록 (한 클릭 롤백/투명성)
- ApiCall: API 관리 직원이 외부에 호출한 모든 트래픽 (감사용)
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean
)
from sqlalchemy.orm import relationship
from .db import Base


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    status = Column(String(16), default="proposed")   # proposed | piloting | active
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    agents = relationship("Agent", back_populates="department")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    role = Column(String(64), nullable=False)         # commander | api_manager | music_producer ...
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    voice = Column(Text, default="")                  # 페르소나 한 줄
    avatar = Column(String(16), default="🍀")          # 메인 콘솔 좌측 패널 표시용
    is_active = Column(Boolean, default=True)
    current_status = Column(String(64), default="대기")  # "대기" | "음악 생성 중" 등
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    department = relationship("Department", back_populates="agents")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    kind = Column(String(64), nullable=False)         # music_generate ...
    department_slug = Column(String(64), nullable=False)
    agent_slug = Column(String(64), nullable=True)
    status = Column(String(32), default="pending")    # pending | running | done | failed
    input = Column(JSON, default=dict)
    output = Column(JSON, default=dict)
    external_id = Column(String(128), nullable=True)  # Mureka task_id 등
    error = Column(Text, default="")
    # 일일 배치 / 검토용
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True, index=True)
    removed_at = Column(DateTime, nullable=True)        # 검토자가 ❌ 누른 시각
    removed_by = Column(String(64), default="")         # owner 또는 telegram chat_id 별칭
    local_audio_path = Column(String(512), default="")  # 다운로드된 mp3 경로 (Phase 3에서 채움)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Batch(Base):
    """일일 자동 배치. 큐레이터 → 작사가 → Mureka 10곡을 묶는다.

    상태:
    - generating       : 큐레이터/작사가/Mureka 생성 중
    - awaiting_review  : 10곡 다 떨어짐. 직원들이 ❌로 빼는 검토창
    - finalizing       : 검토 마감 → ffmpeg 합치기 + YouTube 업로드 중 (Phase 3)
    - uploaded         : YouTube 업로드 완료
    - failed           : 어느 단계든 실패
    """
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True)
    department_slug = Column(String(64), default="music", nullable=False)
    run_date = Column(String(10), index=True, nullable=False)  # 'YYYY-MM-DD'
    status = Column(String(32), default="generating")
    target_count = Column(Integer, default=10)
    curated_themes = Column(JSON, default=list)   # 큐레이터가 뽑은 (issue, concept) 리스트
    deadline_at = Column(DateTime, nullable=True) # 검토 마감 (지나면 finalize)
    youtube_video_id = Column(String(64), default="")
    youtube_url = Column(String(512), default="")
    image_url = Column(String(512), default="")   # AI 생성 정지 이미지
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    actor = Column(String(64), default="owner")       # owner | agent_slug | system
    action = Column(String(128), nullable=False)
    target = Column(String(128), default="")
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ApiCall(Base):
    """API 관리 직원이 남기는 외부 호출 감사 로그.
    토큰/키는 절대 저장하지 않음 (provider 이름만)."""
    __tablename__ = "api_calls"

    id = Column(Integer, primary_key=True)
    provider = Column(String(64), nullable=False)     # mureka | telegram | ...
    operation = Column(String(64), nullable=False)    # generate | query ...
    requester = Column(String(64), default="")        # 어느 직원이 요청했나
    status_code = Column(Integer, default=0)
    ok = Column(Boolean, default=False)
    duration_ms = Column(Integer, default=0)
    request_summary = Column(JSON, default=dict)      # 키/시크릿 제거된 요약만
    response_summary = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Setting(Base):
    """본부 설정 (API 키 포함). is_secret=True면 응답에서 마스킹.
    DB 설정이 env 폴백보다 우선."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False, index=True)
    value = Column(Text, default="")
    is_secret = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
