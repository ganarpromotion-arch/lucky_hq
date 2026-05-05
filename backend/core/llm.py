"""LLM 단일 인터페이스.

원칙 (메모리에 명시):
- 외부 API 키는 'API 관리 직원' = 이 파일만 접근
- 다른 모듈은 provider 이름조차 모르고 tier(T0/T1/T2)만 안다
- T0(무료) → T1(Haiku) → T2(Sonnet) 순으로 자동 폴백
"""
import os
import httpx

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")


def _t0_provider():
    """T0 = 무료. Groq → Gemini Flash 순으로 사용 가능한 것."""
    if GROQ_KEY:
        return ("groq", "llama-3.1-70b-versatile")
    if GEMINI_KEY:
        return ("gemini", "gemini-2.0-flash")
    return None


MODELS = {
    "T0": _t0_provider(),
    "T1": ("anthropic", "claude-haiku-4-5-20251001"),
    "T2": ("anthropic", "claude-sonnet-4-6"),
}


def call_llm(tier: str, system: str, user: str, max_tokens: int = 1500) -> str:
    """tier로만 호출. provider 디테일은 직원이 알 필요 없음."""
    spec = MODELS.get(tier) or MODELS.get("T1")
    if not spec:
        return "[LLM 미설정] 사용 가능한 provider 없음"

    provider, model = spec

    # 키가 없으면 한 단계씩 폴백
    if provider == "anthropic" and not ANTHROPIC_KEY:
        return "[LLM 미설정] ANTHROPIC_API_KEY 누락. Railway Variables에 추가 필요."

    try:
        if provider == "anthropic":
            return _call_anthropic(model, system, user, max_tokens)
        if provider == "gemini":
            return _call_gemini(model, system, user, max_tokens)
        if provider == "groq":
            return _call_groq(model, system, user, max_tokens)
    except httpx.HTTPStatusError as e:
        # T0 실패 → T1로 폴백 한 번
        if tier == "T0":
            return call_llm("T1", system, user, max_tokens)
        return f"[LLM 오류] {e.response.status_code} {e.response.text[:200]}"
    except Exception as e:
        return f"[LLM 오류] {type(e).__name__}: {e}"

    return "[LLM provider 매핑 실패]"


def _call_anthropic(model: str, system: str, user: str, max_tokens: int) -> str:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return "".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    )


def _call_gemini(model: str, system: str, user: str, max_tokens: int) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_KEY}"
    )
    r = httpx.post(
        url,
        json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return "[Gemini 응답 파싱 실패]"


def _call_groq(model: str, system: str, user: str, max_tokens: int) -> str:
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_KEY}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
