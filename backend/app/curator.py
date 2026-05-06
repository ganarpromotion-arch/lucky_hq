"""
큐레이터 직원 (curator)

역할: 매일 최신 이슈/트렌드와 다양한 무드를 종합해, 그 날 만들 곡 N개의
'이슈 + 컨셉' 페어를 LLM으로 큐레이션한다. 작사가가 이를 받아 가사를 쓴다.

흐름:
- LLM (Anthropic Haiku 기본, OpenAI 폴백)에게 다양성 제약을 줘서 N개 산출
- 실패 시 룰 기반으로 무드 분산 + 시즌 키워드 폴백

V1: 외부 트렌드 소스 미연결 — '날짜/계절/요일/공휴일' 컨텍스트만 LLM에 주입
V1.5+: Naver 트렌드 / RSS / 사용자 시드 텍스트 추가 가능
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session

from .api_manager import call_api
from .config import get_settings


def _now_kst() -> datetime:
    """본부 표준 시각 = 한국 표준시. 설정상 timezone을 따라가지만 기본값 KST."""
    tz_name = (get_settings().batch_timezone or "Asia/Seoul")
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(ZoneInfo("Asia/Seoul"))


SEASONS = [
    (3, 5, "봄"),
    (6, 8, "여름"),
    (9, 11, "가을"),
]


def _season(month: int) -> str:
    for lo, hi, name in SEASONS:
        if lo <= month <= hi:
            return name
    return "겨울"


def _today_context() -> dict:
    now = _now_kst()
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    return {
        "date": now.strftime("%Y-%m-%d"),
        "month": now.month,
        "weekday": weekday,
        "season": _season(now.month),
    }


SYSTEM_PROMPT = """너는 한국 K-POP/인디 음악 부서의 '큐레이터'야.
오늘 만들 곡 N개의 '이슈'와 '컨셉'을 다양성 있게 추천한다.

규칙:
- 이슈(issue): 한국어 한 줄. 일상 감정·계절·도시 풍경·소소한 사건·트렌드 등.
  너무 정치적이거나 시사적이지 않게. 보편적이고 음악적으로 풀기 좋은 것.
- 컨셉(concept): 영문 + 한글 혼용 가능. 곡의 무드/장르 방향성을 1~2 문장.
  예: "city pop, 늦여름 오후의 나른함, 92BPM" / "K-ballad, 첫 이별의 잔상".
- N개 사이에 무드/장르/BPM 이 겹치지 않게 분산해라. 발라드만 N개 금지.
- 오늘의 계절·요일을 자연스럽게 일부 곡에 녹여라 (전부는 아니어도 됨).
- 시즌 컨텍스트와 무관한 무드도 섞어라 (다양성).

출력은 반드시 아래 JSON 한 덩어리만. 설명, 코드펜스(```), 추가 텍스트 모두 금지.
{
  "themes": [
    {"issue": "이슈 한 줄", "concept": "컨셉 한 줄"},
    ...
  ]
}
배열은 정확히 N개."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json_block(text: str) -> Optional[str]:
    text = _strip_code_fence(text)
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start: i + 1]
    return None


def _parse_themes(text: str, count: int) -> Optional[list[dict]]:
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        obj = json.loads(block)
    except Exception:
        return None
    themes = obj.get("themes") if isinstance(obj, dict) else None
    if not isinstance(themes, list) or not themes:
        return None
    out: list[dict] = []
    for t in themes:
        if not isinstance(t, dict):
            continue
        issue = (t.get("issue") or "").strip()
        concept = (t.get("concept") or "").strip()
        if issue:
            out.append({"issue": issue, "concept": concept or issue})
    if len(out) < max(1, count // 2):
        return None
    return out[:count]


def _user_prompt(count: int, ctx: dict, seed_text: str = "") -> str:
    base = (
        f"오늘은 {ctx['date']} ({ctx['weekday']}요일, {ctx['season']}).\n"
        f"오늘 만들 곡 N={count}개에 대한 (issue, concept) 페어를 JSON으로 추천해줘."
    )
    if seed_text.strip():
        base += f"\n\n사용자가 준 참고 시드:\n{seed_text.strip()[:600]}"
    return base


async def _llm_curate_anthropic(
    db: Session, count: int, ctx: dict, seed_text: str, model: str
) -> Optional[list[dict]]:
    payload = {
        "model": model,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT.replace("N개", f"{count}개").replace("정확히 N개", f"정확히 {count}개"),
        "messages": [{"role": "user", "content": _user_prompt(count, ctx, seed_text)}],
    }
    result = await call_api(
        db, provider="anthropic", operation="messages",
        payload=payload, requester="curator", timeout=60.0,
    )
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    content = data.get("content") or []
    text = ""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if not text:
        return None
    return _parse_themes(text, count)


async def _llm_curate_openai(
    db: Session, count: int, ctx: dict, seed_text: str, model: str
) -> Optional[list[dict]]:
    payload = {
        "model": model,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.replace("N개", f"{count}개").replace("정확히 N개", f"정확히 {count}개")},
            {"role": "user", "content": _user_prompt(count, ctx, seed_text)},
        ],
        "response_format": {"type": "json_object"},
    }
    result = await call_api(
        db, provider="openai", operation="chat",
        payload=payload, requester="curator", timeout=60.0,
    )
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    choices = data.get("choices") or []
    if not choices:
        return None
    text = ((choices[0] or {}).get("message") or {}).get("content", "")
    return _parse_themes(text, count) if text else None


# 룰 폴백용: 시즌별 이슈 + 무드 셔플
RULE_ISSUE_POOL: dict[str, list[tuple[str, str]]] = {
    "봄": [
        ("벚꽃이 흩날리는 한강 산책", "indie pop, 가벼운 봄 산책, 106 BPM"),
        ("새 학기 첫 등굣길", "K-pop dance, 두근거림, 118 BPM"),
        ("따뜻해진 오후의 카페", "city pop, 봄 오후의 여유, 96 BPM"),
        ("창문을 여는 아침", "lo-fi indie, 부드러운 기상, 82 BPM"),
    ],
    "여름": [
        ("바다에서의 늦은 오후", "tropical house, 여름의 끝, 110 BPM"),
        ("장마가 지나간 거리", "K-ballad, 비 그친 후의 정적, 78 BPM"),
        ("한밤의 야시장", "synthwave, 여름밤 거리, 100 BPM"),
        ("아이스 아메리카노 한 잔", "lo-fi indie, 한낮의 더위, 88 BPM"),
    ],
    "가을": [
        ("단풍이 내려앉은 오후", "city pop, 가을 오후의 따뜻함, 92 BPM"),
        ("선선해진 새벽 출근길", "lo-fi indie, 가을 아침, 84 BPM"),
        ("코트를 꺼내 입는 날", "K-ballad, 환절기의 그리움, 76 BPM"),
        ("야경이 깊어지는 도시", "synthwave, 가을밤 드라이브, 96 BPM"),
    ],
    "겨울": [
        ("첫눈이 내리던 거리", "synthpop, 겨울의 시작, 100 BPM"),
        ("뜨거운 커피 한 모금", "city pop, 겨울 오후의 온기, 90 BPM"),
        ("연말 모임의 들뜬 마음", "K-pop dance, 연말 파티, 124 BPM"),
        ("창밖에 쌓이는 눈", "K-ballad, 조용한 겨울밤, 72 BPM"),
    ],
}

RULE_UNIVERSAL: list[tuple[str, str]] = [
    ("오랜 친구와의 통화", "indie pop, 추억과 웃음, 108 BPM"),
    ("가만히 앉은 도서관", "lo-fi indie, 집중과 정적, 80 BPM"),
    ("새벽 두 시의 도시", "synthwave, 잠 못 드는 밤, 94 BPM"),
    ("좋아하는 사람과의 식사", "city pop, 따뜻한 한 끼, 98 BPM"),
    ("출근길 지하철의 사람들", "lo-fi indie, 도시의 아침, 86 BPM"),
    ("주말의 여유로운 산책", "indie pop, 쉼표 같은 시간, 104 BPM"),
]


def curate_rule(count: int, ctx: dict) -> list[dict]:
    """LLM 실패 폴백. 시즌 풀 + 보편 풀에서 셔플."""
    season = ctx.get("season", "겨울")
    pool = list(RULE_ISSUE_POOL.get(season, RULE_ISSUE_POOL["겨울"])) + list(RULE_UNIVERSAL)
    # 단순 결정적 셔플 (매일 동일한 결과 방지: 날짜로 회전)
    try:
        day = int(ctx["date"][-2:])
    except Exception:
        day = 1
    if pool:
        rotated = pool[day % len(pool):] + pool[: day % len(pool)]
    else:
        rotated = pool
    out: list[dict] = []
    for issue, concept in rotated[:count]:
        out.append({"issue": issue, "concept": concept})
    while len(out) < count and pool:
        # 모자라면 반복 채움
        i = len(out) % len(pool)
        out.append({"issue": pool[i][0], "concept": pool[i][1]})
    return out


async def curate(
    db: Session,
    count: int = 10,
    seed_text: str = "",
) -> dict:
    """오늘의 (issue, concept) N개 큐레이션.

    Returns: {"themes": [{issue, concept}, ...], "source": "llm" | "rule_fallback"}
    """
    ctx = _today_context()
    settings = get_settings()
    model = settings.curator_llm_model

    # 1차: Anthropic
    themes = await _llm_curate_anthropic(db, count, ctx, seed_text, model)
    if not themes:
        # 2차: OpenAI
        themes = await _llm_curate_openai(db, count, ctx, seed_text, "gpt-4o-mini")

    if themes:
        return {"themes": themes[:count], "source": "llm", "context": ctx}

    return {"themes": curate_rule(count, ctx), "source": "rule_fallback", "context": ctx}
