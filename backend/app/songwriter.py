"""
작곡가 직원 (songwriter)

입력: 최근 이슈 (자유 형식 한국어 텍스트)
출력: 제목 / 가사 / 스타일 / 무드 / 키워드

V1.2:
- 우선 LLM(Anthropic Claude 기본, OpenAI 폴백)으로 가사 기획
- LLM 실패 시 룰 기반 휴리스틱으로 폴백 (조사 자동 매칭 포함)
- LLM은 항상 api_manager.call_api()를 통해서만 호출

설정:
- 모델: settings.songwriter_llm_model (기본 claude-haiku-4-5-20251001)
- provider: settings.songwriter_llm_provider (anthropic | openai)
"""
from __future__ import annotations
import json
import re
from typing import Optional
from sqlalchemy.orm import Session

from .api_manager import call_api
from .config import get_settings


def _has_jongseong(ch: str) -> bool:
    """한글 음절의 받침 유무. 한글 음절이 아니면 False."""
    if not ch:
        return False
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return False


def _apply_josa(text: str) -> str:
    """{을/를}, {이/가}, {은/는}, {와/과}, {으로/로} 패턴을 직전 글자 받침에 따라 매칭.
    예: '곱창전골{을/를}' → '곱창전골을',  '커피{을/를}' → '커피를'."""
    pattern = re.compile(r'(.)\{([가-힣]+)/([가-힣]+)\}')
    def sub(m):
        prev_ch, with_jong, without_jong = m.group(1), m.group(2), m.group(3)
        return prev_ch + (with_jong if _has_jongseong(prev_ch) else without_jong)
    return pattern.sub(sub, text)


# ── 스타일 프리셋 (한국 컨텍스트 기반) ──────────────────────────────
PRESETS: list[dict] = [
    {
        "tags": ["가을", "낙엽", "선선", "쌀쌀", "단풍", "10월", "11월"],
        "mood": "warm",
        "style": "city pop, warm female vocal, 92 BPM, autumn vibes, soft saxophone",
    },
    {
        "tags": ["여름", "바다", "휴가", "더위", "장마", "7월", "8월"],
        "mood": "bright",
        "style": "tropical house, bright synth, 112 BPM, summer mood",
    },
    {
        "tags": ["겨울", "눈", "크리스마스", "추위", "12월", "1월", "연말"],
        "mood": "cold",
        "style": "synthpop, cold synth pad, 100 BPM, winter mood, soft chimes",
    },
    {
        "tags": ["봄", "꽃", "벚꽃", "3월", "4월", "5월", "새학기"],
        "mood": "fresh",
        "style": "indie pop, jangly guitar, 106 BPM, spring breeze",
    },
    {
        "tags": ["사랑", "연애", "고백", "이별", "그리움", "마음"],
        "mood": "emotional",
        "style": "K-ballad, emotional female vocal, 76 BPM, piano and strings",
    },
    {
        "tags": ["응원", "파이팅", "힘내", "도전", "수능", "취준", "입시", "이겨"],
        "mood": "energetic",
        "style": "K-pop dance, anthemic chorus, 128 BPM, energetic",
    },
    {
        "tags": ["맛집", "음식", "식당", "메뉴", "전골", "곱창", "진솔정", "안주", "한끼"],
        "mood": "cozy",
        "style": "city pop, smooth groove, 96 BPM, cozy vibe, electric piano",
    },
    {
        "tags": ["아침", "출근", "월요일", "커피", "기상"],
        "mood": "calm",
        "style": "lo-fi indie, mellow keys, 86 BPM, morning",
    },
    {
        "tags": ["밤", "야경", "새벽", "잠", "별", "달"],
        "mood": "dreamy",
        "style": "synthwave, soft pad, 90 BPM, late night drive",
    },
    {
        "tags": ["주말", "친구", "놀러", "여행", "추억"],
        "mood": "playful",
        "style": "indie pop, hand claps, 114 BPM, weekend vibe",
    },
]

DEFAULT_PRESET = {
    "mood": "modern",
    "style": "K-pop, modern production, 102 BPM, polished mix",
}


# ── 무드별 가사 템플릿 ─────────────────────────────────────────────
# {topic} 자리에 이슈 핵심 키워드가 들어감
LYRIC_TEMPLATES: dict[str, str] = {
    "warm": """[Verse 1]
{topic}{을/를} 떠올리는 오후
하늘이 살짝 기울고
바람 한 줌이 닿는다

[Chorus]
{topic}, 우리 사이의 온도
{topic}, 잊혀지지 않는 빛
오늘도 너를 부른다

[Verse 2]
한 걸음 또 한 걸음
{topic}{을/를} 향해 가
이 길의 끝에 너와 있길

[Chorus]
{topic}, 우리 사이의 온도
{topic}, 잊혀지지 않는 빛
오늘도 너를 부른다""",

    "bright": """[Verse 1]
파란 하늘 아래
{topic}{이/가} 부르는 소리
가벼운 발걸음으로

[Chorus]
{topic}, 오늘은 우리의 날
{topic}, 햇살처럼 빛나
멀리 멀리 달려가

[Verse 2]
바람을 타고서
{topic}{과/와} 함께 가는 길
끝나지 않는 여름처럼

[Chorus]
{topic}, 오늘은 우리의 날
{topic}, 햇살처럼 빛나
멀리 멀리 달려가""",

    "cold": """[Verse 1]
{topic}{이/가} 머문 자리에
하얀 입김 하나
조용히 내려앉아

[Chorus]
{topic}, 차가운 손끝에도
{topic}, 따뜻한 마음이
오래도록 남아있어

[Verse 2]
거리는 비어있고
{topic}만 남은 이 밤
한 번 더 너를 부른다

[Chorus]
{topic}, 차가운 손끝에도
{topic}, 따뜻한 마음이
오래도록 남아있어""",

    "fresh": """[Verse 1]
{topic} 가까이에
새로운 계절이 와
오늘은 다른 색으로

[Chorus]
{topic}, 우리의 시작
{topic}, 다시 피어나
한 걸음씩 천천히

[Verse 2]
어제와 다른 오늘
{topic}{이/가} 알려준 길
바람이 가볍게 밀어줘

[Chorus]
{topic}, 우리의 시작
{topic}, 다시 피어나
한 걸음씩 천천히""",

    "emotional": """[Verse 1]
{topic}{을/를} 부르는 밤
가만히 눈을 감으면
그날의 너가 보여

[Chorus]
{topic}, 그 모든 시간이
{topic}, 한 곡의 노래로
지금 내 마음에 남아

[Verse 2]
지금은 멀리 있어도
{topic}{은/는} 변하지 않아
다시 만날 날까지

[Chorus]
{topic}, 그 모든 시간이
{topic}, 한 곡의 노래로
지금 내 마음에 남아""",

    "energetic": """[Verse 1]
{topic}{을/를} 향한 길 위에
주저앉지 않아
한 발 더 내딛는다

[Chorus]
{topic}! 멈추지 마
{topic}! 끝까지 가
오늘이 우리의 무대야

[Verse 2]
숨이 차도 괜찮아
{topic}{을/를} 위한 시간
이 순간이 빛나도록

[Chorus]
{topic}! 멈추지 마
{topic}! 끝까지 가
오늘이 우리의 무대야""",

    "cozy": """[Verse 1]
{topic} 한 입에
하루의 피로가 풀려
이 자리가 좋아져

[Chorus]
{topic}, 오늘의 행복
{topic}, 단순한 기쁨
다시 또 찾아오게 돼

[Verse 2]
조명은 살짝 어둡고
{topic} 향기 가득해
이 시간이 길어지길

[Chorus]
{topic}, 오늘의 행복
{topic}, 단순한 기쁨
다시 또 찾아오게 돼""",

    "calm": """[Verse 1]
{topic}{이/가} 시작되는 아침
조용히 눈을 뜨면
오늘이 와 있어

[Chorus]
{topic}, 천천히 가도 돼
{topic}, 서두르지 않아
나의 속도로 걷는다

[Verse 2]
한 모금의 커피처럼
{topic}{이/가} 스며들고
하루가 부드럽게 열려

[Chorus]
{topic}, 천천히 가도 돼
{topic}, 서두르지 않아
나의 속도로 걷는다""",

    "dreamy": """[Verse 1]
{topic}{이/가} 비추는 밤
도시는 잠들었고
나만 깨어 있어

[Chorus]
{topic}, 별처럼 흩어져
{topic}, 꿈처럼 흐르고
이 밤은 길어지길

[Verse 2]
창밖의 불빛 아래
{topic}{이/가} 떠오를 때
조용히 너를 그려

[Chorus]
{topic}, 별처럼 흩어져
{topic}, 꿈처럼 흐르고
이 밤은 길어지길""",

    "playful": """[Verse 1]
{topic} 함께 가는 길
손을 잡으면 가벼워
오늘은 멈추지 마

[Chorus]
{topic}, 오 우리만의 시간
{topic}, 웃음이 번져
멀리 가도 같이 가

[Verse 2]
사진 한 장 남기고
{topic}{을/를} 기억해 둬
나중에 또 꺼내 보게

[Chorus]
{topic}, 오 우리만의 시간
{topic}, 웃음이 번져
멀리 가도 같이 가""",

    "modern": """[Verse 1]
{topic} 위에 서서
오늘을 그려본다
한 줄의 노래처럼

[Chorus]
{topic}, 우리의 이야기
{topic}, 흔들리지 않아
지금 이 순간이 답이야

[Verse 2]
가까이 또 멀리
{topic}{이/가} 닿는 곳마다
새로운 길이 열려

[Chorus]
{topic}, 우리의 이야기
{topic}, 흔들리지 않아
지금 이 순간이 답이야""",
}


# ── 무드별 제목 패턴 ───────────────────────────────────────────────
TITLE_PATTERNS: dict[str, list[str]] = {
    "warm":      ["{kw}의 색깔", "{kw}, 그날의 온도", "오후의 {kw}"],
    "bright":    ["{kw}, 더 멀리", "햇살 속 {kw}", "{kw} 한 잔"],
    "cold":      ["{kw}, 겨울의 끝", "{kw}이 내릴 때", "한 겹의 {kw}"],
    "fresh":     ["다시, {kw}", "{kw}의 시작", "새 봄, {kw}"],
    "emotional": ["{kw}, 그 마음", "{kw}이라는 이름", "오래된 {kw}"],
    "energetic": ["{kw}, 끝까지", "달려라 {kw}", "{kw}의 무대"],
    "cozy":      ["오늘의 {kw}", "{kw} 한 입", "{kw}, 한 자리"],
    "calm":      ["조용한 {kw}", "{kw}, 천천히", "아침의 {kw}"],
    "dreamy":    ["밤의 {kw}", "{kw}, 별 사이로", "꿈속의 {kw}"],
    "playful":   ["{kw}, 우리 둘", "{kw} 그리고 우리", "주말의 {kw}"],
    "modern":    ["{kw}", "{kw}, 지금", "오늘의 {kw}"],
}


def _pick_preset(text: str) -> dict:
    """가장 많은 태그가 매칭되는 프리셋. 동률이면 PRESETS 순서대로."""
    if not text:
        return DEFAULT_PRESET
    text_lower = text.lower()
    best, best_score = None, 0
    for p in PRESETS:
        score = sum(1 for tag in p["tags"] if tag in text_lower)
        if score > best_score:
            best, best_score = p, score
    return best if best else DEFAULT_PRESET


# 키워드 추출 시 끝에서 떼어낼 조사/어미 (긴 것부터 매칭)
_JOSA_SUFFIXES = sorted(
    ["하는", "이라", "에서", "으로", "한다", "한", "은", "는", "이", "가",
     "을", "를", "와", "과", "의", "에", "도", "만", "고", "도",  "께"],
    key=len, reverse=True,
)


def _strip_josa(word: str) -> str:
    for s in _JOSA_SUFFIXES:
        if word.endswith(s) and len(word) > len(s) + 1:
            return word[: -len(s)]
    return word


def _extract_keyword(text: str) -> str:
    """이슈 텍스트에서 가사·제목에 쓸 핵심 단어를 뽑는다.
    조사/어미 제거 후 가장 긴 토큰. 빈 텍스트면 '오늘'."""
    if not text or not text.strip():
        return "오늘"
    cleaned = re.sub(r"[^\w가-힣\s]", " ", text)
    raw_tokens = [t for t in cleaned.split() if t]
    if not raw_tokens:
        return "오늘"
    # 조사 떼고 후보화
    tokens = [_strip_josa(t) for t in raw_tokens]
    tokens = [t for t in tokens if t]  # 안전망
    # 가장 긴 토큰 (Python sort는 stable)
    tokens.sort(key=lambda t: -len(t))
    kw = tokens[0]
    if len(kw) > 12:
        kw = kw[:12]
    return kw


def compose_plan_rule(issue: str) -> dict:
    """룰 기반 기획 (LLM 폴백용)."""
    issue = (issue or "").strip()
    preset = _pick_preset(issue)
    mood = preset["mood"]
    keyword = _extract_keyword(issue)

    # 가사: 무드 템플릿에 키워드 삽입 후 조사 자동 매칭
    template = LYRIC_TEMPLATES.get(mood, LYRIC_TEMPLATES["modern"])
    lyrics = template.replace("{topic}", keyword)
    lyrics = _apply_josa(lyrics)

    # 제목: 무드별 패턴 첫 번째 사용
    title_patterns = TITLE_PATTERNS.get(mood, TITLE_PATTERNS["modern"])
    title = title_patterns[0].replace("{kw}", keyword)

    return {
        "title": title,
        "lyrics": lyrics,
        "style": preset["style"],
        "mood": mood,
        "keyword": keyword,
        "source": "rule",
    }


# ── LLM 작곡가 ────────────────────────────────────────────────────
SYSTEM_PROMPT = """너는 한국 K-POP/인디 음악 분야의 작곡가야.
사용자가 주는 '최근 이슈' 한 줄을 받아 그 이슈의 무드를 살린 한국어 곡을 기획한다.

규칙:
- 가사는 자연스러운 한국어. 조사(을/를, 이/가, 은/는, 와/과)를 정확히 사용.
- 구조는 [Verse 1] / [Chorus] / [Verse 2] / [Chorus] 4단계.
- 각 섹션은 4행 정도. 너무 길게 쓰지 마.
- 제목은 한국어 8자 이내, 너무 직접적이지 않게.
- 스타일은 영문 키워드 한 줄 (장르 + BPM + 보컬 톤). 예: "city pop, female vocal, 92 BPM, autumn vibes".

출력은 반드시 아래 JSON 한 덩어리만. 설명 텍스트, 코드펜스(```) 모두 금지.
{
  "title": "곡 제목",
  "style": "영문 스타일 키워드",
  "lyrics": "[Verse 1]\\n...\\n\\n[Chorus]\\n...\\n\\n[Verse 2]\\n...\\n\\n[Chorus]\\n...",
  "mood": "warm|bright|cold|fresh|emotional|energetic|cozy|calm|dreamy|playful|modern 중 하나",
  "keyword": "핵심 키워드 한 단어"
}"""


def _strip_code_fence(text: str) -> str:
    """```json ... ``` 같은 코드펜스 제거."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _extract_json_block(text: str) -> Optional[str]:
    """텍스트에서 첫 JSON 객체 블록 추출 (LLM이 앞뒤 잡담 섞을 때 대비)."""
    text = _strip_code_fence(text)
    # 가장 바깥 { ... } 찾기
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
                return text[start : i + 1]
    return None


async def _llm_compose_gemini(db: Session, issue: str, model: str) -> Optional[dict]:
    """Google Gemini로 가사 기획. 무료 티어, 빠름. 실패 시 None."""
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "user": f"이슈: {issue}",
        "max_tokens": 1500,
        "temperature": 0.85,
    }
    result = await call_api(
        db, provider="gemini", operation="generateContent",
        payload=payload, requester="songwriter", timeout=45.0,
    )
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    text = ""
    for p in parts:
        if isinstance(p, dict) and p.get("text"):
            text = p["text"]
            break
    if not text:
        return None
    return _parse_plan_json(text)


async def _llm_compose_anthropic(db: Session, issue: str, model: str) -> Optional[dict]:
    """Anthropic Claude로 가사 기획. 실패 시 None."""
    payload = {
        "model": model,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"이슈: {issue}"}],
    }
    result = await call_api(
        db, provider="anthropic", operation="messages",
        payload=payload, requester="songwriter", timeout=45.0,
    )
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    content = data.get("content") or []
    if not content:
        return None
    # content blocks 중 첫 text 블록
    text = ""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    if not text:
        return None
    return _parse_plan_json(text)


async def _llm_compose_openai(db: Session, issue: str, model: str) -> Optional[dict]:
    """OpenAI ChatCompletions로 가사 기획. 실패 시 None."""
    payload = {
        "model": model,
        "max_tokens": 1500,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"이슈: {issue}"},
        ],
        "response_format": {"type": "json_object"},
    }
    result = await call_api(
        db, provider="openai", operation="chat",
        payload=payload, requester="songwriter", timeout=45.0,
    )
    if not result.get("ok"):
        return None
    data = result.get("data") or {}
    choices = data.get("choices") or []
    if not choices:
        return None
    text = ((choices[0] or {}).get("message") or {}).get("content", "")
    if not text:
        return None
    return _parse_plan_json(text)


def _parse_plan_json(text: str) -> Optional[dict]:
    """LLM 응답 텍스트에서 JSON 추출 + 검증 + 정규화."""
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        obj = json.loads(block)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    title = (obj.get("title") or "").strip()
    style = (obj.get("style") or "").strip()
    lyrics = (obj.get("lyrics") or "").strip()
    if not (title and lyrics):
        return None
    mood = (obj.get("mood") or "modern").strip().lower()
    if mood not in LYRIC_TEMPLATES:
        mood = "modern"
    keyword = (obj.get("keyword") or "").strip() or _extract_keyword(lyrics[:30])
    return {
        "title": title,
        "lyrics": lyrics,
        "style": style or "K-pop, modern production, 100 BPM",
        "mood": mood,
        "keyword": keyword,
        "source": "llm",
    }


async def compose_plan(issue: str, db: Optional[Session] = None) -> dict:
    """이슈 → 기획안.
    설정된 provider를 1차 시도, 실패 시 다른 LLM 시도, 마지막엔 룰 기반 폴백.

    우선순위:
      - songwriter_llm_provider 설정값을 1차로
      - 그 외 사용 가능한 LLM을 차례로 시도
      - 모두 실패 시 룰 기반
    """
    issue = (issue or "").strip()
    if not issue:
        return compose_plan_rule(issue)

    if db is None:
        return compose_plan_rule(issue)

    settings = get_settings()
    provider = (settings.songwriter_llm_provider or "gemini").lower()
    primary_model = settings.songwriter_llm_model

    # provider별 호출 함수
    async def try_gemini(model: str = "gemini-2.5-flash") -> Optional[dict]:
        return await _llm_compose_gemini(db, issue, model)

    async def try_anthropic(model: str = "claude-haiku-4-5-20251001") -> Optional[dict]:
        return await _llm_compose_anthropic(db, issue, model)

    async def try_openai(model: str = "gpt-4o-mini") -> Optional[dict]:
        return await _llm_compose_openai(db, issue, model)

    # 시도 순서 결정 — 설정된 provider가 1순위
    plan: Optional[dict] = None
    tried: list[str] = []

    order = [provider]
    for p in ("gemini", "anthropic", "openai"):
        if p not in order:
            order.append(p)

    for p in order:
        try:
            if p == "gemini":
                plan = await try_gemini(primary_model if provider == "gemini" else "gemini-2.5-flash")
            elif p == "anthropic":
                plan = await try_anthropic(primary_model if provider == "anthropic" else "claude-haiku-4-5-20251001")
            elif p == "openai":
                plan = await try_openai(primary_model if provider == "openai" else "gpt-4o-mini")
            tried.append(p)
            if plan:
                plan["llm_provider"] = p
                return plan
        except Exception:
            tried.append(f"{p}!")
            continue

    # 모두 실패 → 룰 폴백
    out = compose_plan_rule(issue)
    out["source"] = "rule_fallback"
    out["llm_tried"] = tried
    return out
