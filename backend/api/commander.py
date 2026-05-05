"""Commander = 자연어 명령 단일 진입점.

V1: 직원 추가 마법사용 'suggest-module' (자연어 → 모듈 슬러그 추천).
V2 예정: 의도 분류 → 안전 게이트 → 디스패치 (텔레그램/콘솔 공통).
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from backend.db import get_session
from backend.core.llm import call_llm
from backend.agents.registry import all_modules

router = APIRouter(prefix="/api/commander", tags=["commander"])


class IntentRequest(BaseModel):
    text: str


def _extract_json(text: str) -> dict | None:
    """텍스트에서 첫 번째 균형잡힌 {...} 블록을 찾아 파싱.
    `re.search(r'\\{.*\\}')` 는 중첩이나 마크다운 펜스에 약해서 직접 brace 매칭.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == "\\":
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _pick(d: dict, *keys, default=None):
    """여러 가능한 키 이름 중 첫 번째 truthy 값."""
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


@router.post("/suggest-module")
def suggest_module(req: IntentRequest, db: Session = Depends(get_session)) -> dict:
    """자연어 직원 추가 요청 → 카탈로그에서 가장 잘 맞는 모듈 추천.

    응답 형식 (정규화):
      {module, fit, name_suggestion, reason, raw}
    raw는 항상 포함 — 디버깅과 투명성.
    """
    modules = all_modules()
    if not modules:
        return {
            "module": None, "fit": "none",
            "name_suggestion": "", "reason": "등록된 모듈 없음", "raw": "",
        }

    catalog = "\n".join(
        f"- {m.slug}: {m.label} — {m.description}" for m in modules
    )
    valid_slugs = ", ".join(m.slug for m in modules)

    sys = (
        "너는 회사 운영 보조다. 사용자의 요청을 듣고 아래 직원 카탈로그에서 "
        "가장 적합한 모듈 하나를 고른다.\n\n"
        "출력 규칙 (반드시 지켜라):\n"
        "1. JSON 객체 하나만 출력한다. 어떤 마크다운, 설명, 코드펜스도 추가하지 마라.\n"
        "2. 키 이름은 정확히 다음 네 개만 사용: module, fit, name_suggestion, reason\n"
        f"3. module 값은 카탈로그의 slug 중 하나여야 한다 (가능한 값: {valid_slugs}).\n"
        "4. fit 값은 'high', 'medium', 'low' 중 하나.\n"
        "5. 사용자 요청에 여러 일이 섞여 있으면, 가장 핵심적인 첫 번째 일에 맞는 모듈을 고른다.\n"
        "6. 카탈로그 어느 것과도 안 맞으면 가장 가까운 것을 고르고 fit는 'low'.\n\n"
        '예시 출력: {"module":"issue_scout","fit":"high",'
        '"name_suggestion":"매일 이슈 스카우트","reason":"키워드 기반 이슈 탐색·요약에 정확히 부합"}'
    )
    user = f"카탈로그:\n{catalog}\n\n사용자 요청:\n{req.text}"

    raw = call_llm(tier="T1", system=sys, user=user, max_tokens=400)

    parsed = _extract_json(raw)
    if not parsed:
        return {
            "module": None, "fit": "low",
            "name_suggestion": "",
            "reason": "LLM 응답에서 JSON을 찾지 못함",
            "raw": raw,
        }

    # 키 이름이 약간 달라도(selected_module 등) 받아준다.
    chosen = _pick(parsed, "module", "slug", "selected_module", "module_slug")
    valid = {m.slug for m in modules}
    if chosen not in valid:
        # LLM이 이상한 slug를 줬으면 무효 처리하되 raw는 보여줌
        chosen = None

    return {
        "module": chosen,
        "fit": _pick(parsed, "fit", "confidence", "match", default="?"),
        "name_suggestion": _pick(
            parsed, "name_suggestion", "name", "suggested_name", "title", default=""
        ),
        "reason": _pick(
            parsed, "reason", "reasoning", "rationale", "why", default=""
        ),
        "raw": raw,
    }
