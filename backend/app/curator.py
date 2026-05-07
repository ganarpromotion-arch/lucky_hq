"""
큐레이터 직원 (curator)

두 가지 모드:
  1. 자동 배치용 — pick_issues(N): 풀에서 N개 픽 (기존 유지, 빠름)
  2. 수동 단발용 — propose_options(): Gemini로 언어/분위기/키워드 5개씩 제안 (5×3 안)

V1.5+: 외부 트렌드 API (네이버 데이터랩 / Google Trends KR) 연결 가능.
지금은 LLM이 일반적 트렌드 + 한국 5월 컨텍스트 + 진솔정 도메인을 알고 있다고 가정.
"""
from __future__ import annotations
import json
import logging
import random
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import desc
from .api_manager import call_api
from .config import get_settings
from .models import CuratorLesson

log = logging.getLogger("lucky_hq.curator")


# 한국어 이슈 풀 (자동 배치용 — LLM 없이 빠른 픽)
ISSUE_POOL: list[str] = [
    "진솔정 가을 메뉴 곱창전골",
    "진솔정 한 잔의 막걸리",
    "진솔정 가게 앞 골목의 저녁",
    "곱창전골 한 점의 위로",
    "주말 저녁 친구들과 진솔정",
    "오월의 햇살 가득한 오후",
    "초여름 바람이 부는 거리",
    "비 오는 날 창가의 커피",
    "초록이 짙어지는 5월",
    "장마 시작 전 마지막 푸른 날",
    "월요일 아침의 출근길",
    "퇴근 후 마시는 한 잔",
    "오랜만에 만난 친구와 산책",
    "주말 늦은 아침의 여유",
    "혼자 듣는 새벽의 라디오",
    "힘든 한 주를 버텨낸 너에게",
    "다시 시작하는 용기",
    "포기하지 않는 마음",
    "오래전 그 사람 생각",
    "닿지 못한 마음 한 줄",
    "한강을 따라 걷는 저녁",
    "야경이 아름다운 옥상",
    "지하철 막차의 풍경",
    "동네 단골 카페의 아메리카노",
    "주말 짧은 여행의 첫 새벽",
]


def pick_issues(n: int = 6, seed: int | None = None) -> list[str]:
    rng = random.Random(seed) if seed is not None else random.Random()
    pool = list(ISSUE_POOL)
    rng.shuffle(pool)
    return pool[: max(1, min(n, len(pool)))]


def today_seed() -> int:
    return int(datetime.now().strftime("%Y%m%d"))


# ── 5×3 안 제안 (Gemini 사용) ───────────────────────────
CURATOR_SYSTEM = """너는 한국 음악 트렌드 큐레이터야.
사용자가 곡을 만들기 직전, 곡의 방향을 정할 수 있도록 5x3개 옵션을 제안한다.

오늘은 {today}이고, 한국 기준 시즌과 날씨, 일반적인 분위기를 반영해.
진솔정(곱창전골 가게) 마케팅 곡일 수도 있으니 그 컨텍스트도 일부 옵션에 녹여라.

출력은 반드시 아래 JSON 한 덩어리만. 설명/코드펜스 금지.
{{
  "languages": ["한국어 (서정적)", "한국어 (감각적)", "한국어 + 영어 믹스", "영어", "한국어 (담백)"],
  "moods": ["잔잔한 위로", "에너지 넘치는 응원", "감성적인 추억", "발랄한 일상", "어쿠스틱 감성"],
  "keywords": ["진솔정 곱창전골 저녁", "5월 출근길", "주말 늦은 아침", "한강 야경", "비 오는 날 카페"]
}}

옵션은 매번 다양하게. 너무 비슷한 항목은 피하고, 한국 5월(초여름 진입) 무드를 반영해."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json(text: str) -> Optional[str]:
    text = _strip_code_fence(text)
    depth = 0; start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]
    return None


def _fallback_options() -> dict:
    """LLM 실패 시 기본 옵션 (계절 무관 안전한 세트)."""
    return {
        "languages": [
            "한국어 (서정적)",
            "한국어 (감각적)",
            "한국어 + 영어 믹스",
            "영어",
            "한국어 (담백)",
        ],
        "moods": [
            "잔잔한 위로",
            "에너지 넘치는 응원",
            "감성적인 추억",
            "발랄한 일상",
            "어쿠스틱 감성",
        ],
        "keywords": [
            "진솔정 곱창전골 저녁",
            "5월 출근길",
            "주말 늦은 아침",
            "한강 야경",
            "비 오는 날 카페",
        ],
        "source": "fallback",
    }


def _build_lessons_block(db: Session) -> tuple[str, list[int]]:
    """active 큐레이터 교육 자료를 system prompt에 끼워 넣을 텍스트로 변환.
    weight 큰 것부터, 각 kind별 그룹핑. 사용된 lesson id 목록도 반환 (used_count 갱신용)."""
    rows = (
        db.query(CuratorLesson)
        .filter(CuratorLesson.active.is_(True))
        .order_by(desc(CuratorLesson.weight), desc(CuratorLesson.id))
        .limit(40)
        .all()
    )
    if not rows:
        return "", []
    by_kind: dict[str, list[CuratorLesson]] = {}
    for r in rows:
        by_kind.setdefault(r.kind, []).append(r)

    titles = {
        "prefer":  "owner가 좋아하는 방향 (반드시 반영)",
        "avoid":   "owner가 싫어하는 패턴 (피할 것)",
        "example": "참고 예시 (이런 결과를 더)",
        "rule":    "원칙 (어기면 안 됨)",
    }
    parts = ["", "── 큐레이터 교육 메모 ──"]
    for kind in ("rule", "avoid", "prefer", "example"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        parts.append(f"\n[{titles.get(kind, kind)}]")
        for r in items:
            star = "★" * max(1, min(5, r.weight or 1))
            parts.append(f"- ({star}) {r.text.strip()[:300]}")
    return "\n".join(parts), [r.id for r in rows]


async def propose_options(db: Session) -> dict:
    """5x3 안 제안 (Gemini → 폴백). 큐레이터 교육 메모 자동 주입."""
    settings = get_settings()
    today = datetime.now().strftime("%Y년 %m월 %d일 (%a)")
    system = CURATOR_SYSTEM.format(today=today)
    lessons_block, lesson_ids = _build_lessons_block(db)
    if lessons_block:
        system = system + "\n" + lessons_block

    payload = {
        "model": "gemini-2.5-flash",
        "system": system,
        "user": "오늘 만들 곡의 방향 5x3 옵션을 제안해줘.",
        "max_tokens": 800,
        "temperature": 1.0,
    }
    result = await call_api(db, provider="gemini", operation="generateContent",
                            payload=payload, requester="curator", timeout=30.0)

    if not result.get("ok"):
        out = _fallback_options()
        out["error"] = result.get("error", "")[:200]
        return out

    data = result.get("data") or {}
    candidates = data.get("candidates") or []
    text = ""
    if candidates:
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        for p in parts:
            if isinstance(p, dict) and p.get("text"):
                text = p["text"]; break

    block = _extract_json(text) if text else None
    if not block:
        return _fallback_options()
    try:
        obj = json.loads(block)
    except Exception:
        return _fallback_options()

    # 검증 + 정규화
    langs = obj.get("languages") or []
    moods = obj.get("moods") or []
    keywords = obj.get("keywords") or []
    if not (isinstance(langs, list) and isinstance(moods, list) and isinstance(keywords, list)):
        return _fallback_options()

    # 5개로 자르기/채우기
    fb = _fallback_options()
    def normalize(arr: list, fallback_arr: list) -> list[str]:
        clean = [str(x).strip() for x in arr if x and str(x).strip()][:5]
        while len(clean) < 5:
            clean.append(fallback_arr[len(clean) % len(fallback_arr)])
        return clean

    # 교육 메모 사용 카운트 + 마지막 사용 시각 업데이트
    if lesson_ids:
        try:
            now = datetime.utcnow()
            for r in db.query(CuratorLesson).filter(CuratorLesson.id.in_(lesson_ids)).all():
                r.used_count = (r.used_count or 0) + 1
                r.last_used_at = now
            db.commit()
        except Exception:
            db.rollback()

    return {
        "languages": normalize(langs, fb["languages"]),
        "moods": normalize(moods, fb["moods"]),
        "keywords": normalize(keywords, fb["keywords"]),
        "source": "llm",
        "today": today,
        "lessons_applied": len(lesson_ids),
    }


def combine_choice(language: str, mood: str, keyword: str) -> str:
    """선택된 3개 → 작곡가가 받을 단일 issue 문자열."""
    return f"{keyword} | 분위기: {mood} | 언어: {language}"
