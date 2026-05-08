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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 배치/검토
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True, index=True)
    # pending_review | approved | rejected | none
    review_status = Column(String(32), default="none")
    telegram_message_id = Column(Integer, nullable=True)  # owner 채널에 보낸 메시지 id

    # 로컬 보관 (사이트에서 재생용)
    local_audio_path = Column(String(512), default="")    # 다운로드된 mp3/wav 경로
    local_audio_size = Column(Integer, default=0)         # bytes
    archived_at = Column(DateTime, nullable=True)         # 다운로드 완료 시각
    deleted_at = Column(DateTime, nullable=True)          # 삭제 시각 (소프트 딜리트)


class Batch(Base):
    """배치 작업 단위 — 곡 N개 묶음.
    트리거(test_button | scheduled | manual)와 결과 요약 보유."""
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True)
    department_slug = Column(String(64), nullable=False, index=True)
    kind = Column(String(64), nullable=False)              # daily_music_6
    trigger = Column(String(32), default="manual")         # test_button | scheduled | manual
    status = Column(String(32), default="pending")         # pending | running | reporting | done | failed
    target_count = Column(Integer, default=6)
    completed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    issues = Column(JSON, default=list)                    # 큐레이터가 뽑은 이슈 N개
    make_video = Column(Boolean, default=False)            # 곡마다 mp4 만들지 여부
    error = Column(Text, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


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


class Video(Base):
    """영상 작업 단위 — 곡 1개 → mp4 1편.
    원본 곡 Job 참조 + 결과 파일 경로 + 텔레그램 전송 상태."""
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True, index=True)
    status = Column(String(32), default="pending")    # pending | rendering | done | failed
    image_path = Column(String(512), default="")      # 정지 이미지 결과 경로
    video_path = Column(String(512), default="")      # mp4 결과 경로
    duration_sec = Column(Integer, default=0)
    file_size = Column(Integer, default=0)
    telegram_sent = Column(Boolean, default=False)
    telegram_message_id = Column(Integer, nullable=True)
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TelegramSubscriber(Base):
    """텔레그램으로 본부에 가입한 사용자.

    역할 6단계 (메모리 정책):
      - owner       : 모든 권한
      - manager     : 최고 팀장 — owner와 동일한 메시지/권한
      - operator    : 작업 실행 가능
      - approver    : 승인만 가능
      - viewer      : 보고만 받음
      - guest       : 등록만 됨
    """
    __tablename__ = "telegram_subscribers"

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(64), unique=True, nullable=False, index=True)
    role = Column(String(32), default="viewer")  # owner | manager | operator | approver | viewer | guest
    nickname = Column(String(128), default="")
    username = Column(String(128), default="")   # 텔레그램 @username
    first_name = Column(String(128), default="")
    is_active = Column(Boolean, default=True)
    receives_song_reports = Column(Boolean, default=True)   # 곡 보고 받음
    receives_chat_replies = Column(Boolean, default=True)   # 챗봇 응답 가능
    joined_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)


class TelegramJoinCode(Base):
    """1회용 가입 코드 (owner가 발급, 멤버가 봇에 /join CODE)."""
    __tablename__ = "telegram_join_codes"

    id = Column(Integer, primary_key=True)
    code = Column(String(16), unique=True, nullable=False, index=True)
    role = Column(String(32), default="manager")  # 발급 시 부여될 역할
    used = Column(Boolean, default=False)
    used_by_chat_id = Column(String(64), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)


class CuratorLesson(Base):
    """큐레이터 교육 자료 — owner가 가르치는 취향/금지/예시 메모.

    kind:
      - prefer    : "이런 무드/키워드 좋아함"
      - avoid     : "이런 패턴 싫음"
      - example   : "이 곡 같은 분위기로 더"
      - rule      : "원칙 — 진솔정 키워드는 한 주에 1번만 쓰기" 등
    propose_options 호출 시 active=True 인 lesson을 system prompt에 주입한다.
    """
    __tablename__ = "curator_lessons"

    id = Column(Integer, primary_key=True)
    kind = Column(String(16), nullable=False, default="prefer", index=True)
    text = Column(Text, nullable=False)             # 짧은 문장 (한국어 OK)
    weight = Column(Integer, default=1)             # 1~5, 클수록 강하게 강조
    active = Column(Boolean, default=True, index=True)
    created_by = Column(String(64), default="owner")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    used_count = Column(Integer, default=0)
    last_used_at = Column(DateTime, nullable=True)


class DailyProposal(Base):
    """매일 아침 8시 큐레이터가 만든 5x3 안 + owner 응답.

    상태:
      - waiting   : 발송했지만 응답 대기
      - chosen    : owner가 골랐고 배치 트리거됨
      - skipped   : 응답 없이 다음 날까지 넘어감 (패스)
      - cancelled : owner가 명시적으로 취소
    """
    __tablename__ = "daily_proposals"

    id = Column(Integer, primary_key=True)
    date_kst = Column(String(16), nullable=False, index=True)  # "2026-05-07"
    languages = Column(JSON, default=list)
    moods = Column(JSON, default=list)
    keywords = Column(JSON, default=list)
    status = Column(String(32), default="waiting")
    chosen_language_idx = Column(Integer, nullable=True)
    chosen_mood_idx = Column(Integer, nullable=True)
    chosen_keyword_idx = Column(Integer, nullable=True)
    chosen_by_chat_id = Column(String(64), nullable=True)
    triggered_batch_id = Column(Integer, nullable=True)
    telegram_message_ids = Column(JSON, default=list)
    sent_at = Column(DateTime, nullable=True)
    chosen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
