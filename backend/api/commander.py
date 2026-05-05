"""Commander = 자연어 명령 단일 진입점.

V1: 직원 추가 마법사용 'suggest-module' (자연어 → 모듈 슬러그 추천).
V2 예정: 의도 분류 → 안전 게이트 → 디스패치 (텔레그램/콘솔 공통).
"""
import json
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from backend.db import get_session
from backend.core.llm import call_llm
from backend.agents.registry import all_modules

router = APIRouter(prefix="/api/commander", tags=["commander"])


class IntentRequest(BaseModel):
    text: str


@router.post("/suggest-module")
def suggest_module(req: IntentRequest, db: Session = Depends(get_session)) -> dict:
    """자연어 직원 추가 요청 → 카탈로그에서 가장 잘 맞는 모듈 추천."""
    modules = all_modules()
    if not modules:
        return {"module": None, "fit": "none", "reason": "등록된 모듈 없음"}

    catalog = "\n".join(
        f"- {m.slug}: {m.label} — {m.description}" for m in modules
    )

    sys = (
        "너는 회사 운영 보조다. 사용자가 만들고 싶어하는 직원을 듣고 "
        "아래 모듈 카탈로그에서 가장 잘 맞는 slug 하나를 고른다. "
        "딱 맞는 게 없어도 가장 가까운 것을 고르되 'fit' 값으로 신뢰도를 표시한다. "
        "JSON만 출력하라. 다른 말 금지. "
        '형식: {"module":"slug","fit":"high|medium|low","name_suggestion":"한국어 이름","reason":"한 문장"}'
    )
    user = f"카탈로그:\n{catalog}\n\n사용자 요청: {req.text}"

    raw = call_llm(tier="T1", system=sys, user=user, max_tokens=400)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"module": None, "fit": "low", "reason": "LLM 응답 파싱 실패", "raw": raw}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"module": None, "fit": "low", "reason": "JSON 파싱 실패", "raw": raw}
