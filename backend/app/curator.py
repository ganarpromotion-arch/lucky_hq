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


# 수면·앰비언트 장면 풀 (자동 배치용 — LLM 없이 빠른 픽, 전부 영어/글로벌)
ISSUE_POOL: list[str] = [
    "Rainy Night", "Moonlit Room", "Quiet Snowfall", "Cozy Fireplace",
    "Ocean at Night", "Misty Forest Dawn", "Starry Sky", "Autumn Rain",
    "Candlelight Evening", "Distant Thunder", "Winter Window", "Late Night Calm",
    "Soft Morning Light", "Peaceful Lake", "Gentle Snowstorm", "Dreaming Softly",
    "Drifting Clouds", "Warm Cabin", "Midnight Piano", "Falling Leaves",
]


def pick_issues(n: int = 6, seed: int | None = None) -> list[str]:
    rng = random.Random(seed) if seed is not None else random.Random()
    pool = list(ISSUE_POOL)
    rng.shuffle(pool)
    return pool[: max(1, min(n, len(pool)))]


def today_seed() -> int:
    return int(datetime.now().strftime("%Y%m%d"))


# ── 5×3 안 제안 (Gemini 사용) ───────────────────────────
CURATOR_SYSTEM = """You are a curator for a GLOBAL sleep & ambient music YouTube channel (Healing Waves).
Before a relaxing instrumental track (piano / ambient) is made, propose 5x3 options so the owner can pick a direction.
Focus on calm, cozy, sleepy SCENES and MOODS. Everything must be in ENGLISH (global channel). Today is {today}.

Output ONLY this JSON block. No explanation, no code fence.
{{
  "languages": ["Piano", "Ambient", "Lo-fi", "Nature", "Cinematic"],
  "moods": ["Calm & Peaceful", "Warm & Cozy", "Dreamy & Soft", "Melancholic & Tender", "Serene & Still"],
  "keywords": ["Rainy Night", "Moonlit Room", "Quiet Snowfall", "Cozy Fireplace", "Ocean at Night"]
}}

Vary the options each time. Keep them evocative sleep/relaxation scenes. Avoid anything energetic, loud, or food/marketing related."""


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
        "languages": ["Piano", "Ambient", "Lo-fi", "Nature", "Cinematic"],
        "moods": [
            "Calm & Peaceful",
            "Warm & Cozy",
            "Dreamy & Soft",
            "Melancholic & Tender",
            "Serene & Still",
        ],
        "keywords": [
            "Rainy Night",
            "Moonlit Room",
            "Quiet Snowfall",
            "Cozy Fireplace",
            "Ocean at Night",
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
        "concept": "★ 부서 기본 컨셉 (모든 곡의 출발점)",
        "prefer":  "owner가 좋아하는 방향 (반드시 반영)",
        "avoid":   "owner가 싫어하는 패턴 (피할 것)",
        "example": "참고 예시 (이런 결과를 더)",
        "rule":    "원칙 (어기면 안 됨)",
    }
    parts = ["", "── 큐레이터 교육 메모 ──"]
    for kind in ("concept", "rule", "avoid", "prefer", "example"):
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
