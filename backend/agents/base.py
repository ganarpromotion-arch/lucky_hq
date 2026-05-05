"""직원 모듈 베이스.

한 모듈 = 한 직원 타입 (예: 'issue_scout', 'writer', 'shorts_maker').
새 직원 타입을 추가하려면 builtins/<slug>.py 만들고 @register만 붙이면 끝.
"""
from abc import ABC, abstractmethod
from typing import ClassVar


class AgentModule(ABC):
    # 식별자 (DB의 Agent.module 과 매칭)
    slug: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""

    # 마법사가 사용자에게 받을 항목들
    # 예: [{"key": "keywords", "label": "키워드", "type": "text", "required": True}]
    config_schema: ClassVar[list[dict]] = []

    # 직원 생성 시 기본값
    default_role_prompt: ClassVar[str] = ""
    default_llm_tier: ClassVar[str] = "T1"
    default_schedule_cron: ClassVar[str] = ""

    @abstractmethod
    def do_work(self, agent, db, ctx: dict) -> dict:
        """실제 작업.
        반환값(dict)은 Job.output에 그대로 저장됨.
        """
        ...
