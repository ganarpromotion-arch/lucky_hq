"""
API 관리 직원 (api_manager)

본부 정책:
- 모든 외부 API 호출은 이 모듈을 통해서만 나간다.
- 다른 직원/부서는 provider 이름만 알고, 실제 키/시크릿/세션은 모른다.
- 모든 호출은 ApiCall 테이블에 감사 기록이 남는다 (단, 키/토큰은 절대 저장 안 됨).
- 키는 DB(Setting 테이블) 우선, 없으면 환경변수로 폴백.

V1에서는 Mureka 한 곳만 연결. v1.5에서 텔레그램/Claude/Gemini 추가.
"""
from __future__ import annotations
import time
from typing import Any
import httpx
from sqlalchemy.orm import Session

from .config import get_settings
from .models import ApiCall, Setting


class ProviderError(Exception):
    pass


def _redact(d: dict) -> dict:
    """요약에서 민감 키 제거"""
    if not isinstance(d, dict):
        return {}
    bad = {"authorization", "api_key", "token", "secret", "password", "key"}
    return {k: ("***" if k.lower() in bad else v) for k, v in d.items()}


def _resolve_secret(db: Session, db_key: str, env_value: str) -> str:
    """DB Setting → env 순으로 키 찾기."""
    try:
        row = db.query(Setting).filter_by(key=db_key).first()
        if row and row.value:
            return row.value
    except Exception:
        pass
    return env_value or ""


async def call_api(
    db: Session,
    provider: str,
    operation: str,
    payload: dict | None = None,
    requester: str = "",
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    외부 API 단일 호출 인터페이스.

    Returns: {"ok": bool, "data": ..., "status_code": int, "error": str}
    """
    settings = get_settings()
    payload = payload or {}
    started = time.time()

    # 라우팅: provider별 실제 호출
    try:
        if provider == "mureka":
            result = await _call_mureka(db, operation, payload, settings, timeout)
        elif provider == "anthropic":
            result = await _call_anthropic(db, operation, payload, settings, timeout)
        elif provider == "openai":
            result = await _call_openai(db, operation, payload, settings, timeout)
        elif provider == "gemini":
            result = await _call_gemini(db, operation, payload, settings, timeout)
        elif provider == "telegram":
            result = await _call_telegram(db, operation, payload, settings, timeout)
        else:
            raise ProviderError(f"unknown provider: {provider}")

        ok = result.get("ok", False)
        status_code = int(result.get("status_code", 0))
        error = "" if ok else str(result.get("error", ""))
    except Exception as e:
        ok = False
        status_code = 0
        error = str(e)
        result = {"ok": False, "error": error, "status_code": 0, "data": None}

    duration_ms = int((time.time() - started) * 1000)

    # 감사 기록
    try:
        log = ApiCall(
            provider=provider,
            operation=operation,
            requester=requester,
            status_code=status_code,
            ok=ok,
            duration_ms=duration_ms,
            request_summary=_redact(payload),
            response_summary=_redact(result.get("data") or {}) if isinstance(result.get("data"), dict) else {},
        )
        db.add(log)
        db.commit()
    except Exception:
        db.rollback()

    return result


def _resolve_int_setting(db: Session, db_key: str, default_value: int) -> int:
    raw = _resolve_secret(db, db_key, "")
    try:
        return int(raw) if raw else int(default_value)
    except (TypeError, ValueError):
        return int(default_value)


def _apply_mureka_defaults(db: Session, payload: dict, settings) -> dict:
    """generate payload에 model/n/max_duration_sec 기본값 채우기.
    DB Setting > 환경/기본값 순. 호출자가 이미 명시한 값은 유지."""
    out = dict(payload or {})

    # 모델: "auto" | "mureka-7.5" | "mureka-v8" | "mureka-v9" 등
    if not out.get("model"):
        model = _resolve_secret(db, "mureka_model", settings.mureka_model) or "auto"
        out["model"] = model

    # 곡 수: 1~3, 기본 2
    if "n" not in out:
        n = _resolve_int_setting(db, "mureka_n", settings.mureka_n)
        out["n"] = max(1, min(3, n))

    # 길이 상한: 0~330초 (5m30s)
    if "max_duration_sec" not in out and "duration" not in out:
        cap = _resolve_int_setting(db, "mureka_max_duration_sec", settings.mureka_max_duration_sec)
        cap = max(0, min(330, cap))
        if cap > 0:
            out["max_duration_sec"] = cap

    return out


async def _call_mureka(
    db: Session, operation: str, payload: dict, settings, timeout: float
) -> dict[str, Any]:
    """Mureka API 라우팅."""
    api_key = _resolve_secret(db, "mureka_api_key", settings.mureka_api_key)
    if not api_key:
        return {"ok": False, "error": "Mureka API 키 미등록 — /dept/music 페이지에서 등록하세요", "status_code": 0, "data": None}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base = settings.mureka_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout) as client:
        if operation == "generate":
            # 기본 옵션 병합: 호출자가 model/n/max_duration_sec 명시하지 않으면 설정값으로 채움
            payload = _apply_mureka_defaults(db, payload, settings)
            # POST /v1/song/generate  (memory 기준)
            r = await client.post(f"{base}/v1/song/generate", headers=headers, json=payload)
        elif operation == "query":
            task_id = payload.get("id") or payload.get("task_id")
            if not task_id:
                return {"ok": False, "error": "id 누락", "status_code": 0, "data": None}
            r = await client.get(f"{base}/v1/song/query/{task_id}", headers=headers)
        else:
            return {"ok": False, "error": f"unknown op: {operation}", "status_code": 0, "data": None}

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}

    # 친절한 에러 메시지 — Mureka가 주는 메시지를 추출
    if r.is_success:
        error_msg = ""
    else:
        api_msg = ""
        if isinstance(data, dict):
            # Mureka 응답 후보 키 다수 처리
            api_msg = (
                data.get("message")
                or data.get("error")
                or (data.get("error", {}) if isinstance(data.get("error"), dict) else {}).get("message", "")
                or data.get("detail")
                or ""
            )
            if isinstance(api_msg, dict):
                api_msg = api_msg.get("message", str(api_msg))
        prefix = {
            401: "Mureka 키 인증 실패",
            403: "Mureka 권한 없음",
            429: "Mureka 사용 한도 초과 또는 동시 호출 제한",
        }.get(r.status_code, f"Mureka HTTP {r.status_code}")
        error_msg = f"{prefix}{' — ' + str(api_msg)[:200] if api_msg else ''}"

    return {
        "ok": r.is_success,
        "status_code": r.status_code,
        "data": data,
        "error": error_msg,
    }


async def _call_anthropic(
    db: Session, operation: str, payload: dict, settings, timeout: float
) -> dict[str, Any]:
    """Anthropic Claude Messages API.

    operation:
      - "messages"  : POST /v1/messages
    """
    api_key = _resolve_secret(db, "anthropic_api_key", settings.anthropic_api_key)
    if not api_key:
        return {"ok": False, "error": "Anthropic API 키 미설정 (env ANTHROPIC_API_KEY)", "status_code": 0, "data": None}

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    base = settings.anthropic_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout) as client:
        if operation == "messages":
            r = await client.post(f"{base}/v1/messages", headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"unknown op: {operation}", "status_code": 0, "data": None}

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}

    return {
        "ok": r.is_success,
        "status_code": r.status_code,
        "data": data,
        "error": "" if r.is_success else f"HTTP {r.status_code}: {str(data)[:200]}",
    }


async def _call_openai(
    db: Session, operation: str, payload: dict, settings, timeout: float
) -> dict[str, Any]:
    """OpenAI Chat Completions (폴백용)."""
    api_key = _resolve_secret(db, "openai_api_key", settings.openai_api_key)
    if not api_key:
        return {"ok": False, "error": "OpenAI API 키 미설정", "status_code": 0, "data": None}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base = settings.openai_base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=timeout) as client:
        if operation == "chat":
            r = await client.post(f"{base}/v1/chat/completions", headers=headers, json=payload)
        else:
            return {"ok": False, "error": f"unknown op: {operation}", "status_code": 0, "data": None}

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}

    return {
        "ok": r.is_success,
        "status_code": r.status_code,
        "data": data,
        "error": "" if r.is_success else f"HTTP {r.status_code}: {str(data)[:200]}",
    }


async def _call_gemini(
    db: Session, operation: str, payload: dict, settings, timeout: float
) -> dict[str, Any]:
    """Google Gemini API.

    operation:
      - "generateContent" : POST /v1beta/models/{model}:generateContent
        payload: {"model": "gemini-2.5-flash", "system": "...", "user": "...", "max_tokens": 1500}

    무료 티어: 분당 15회, 일일 1500회.
    """
    api_key = _resolve_secret(db, "gemini_api_key", settings.gemini_api_key)
    if not api_key:
        return {"ok": False, "error": "Gemini API 키 미설정 (GEMINI_API_KEY)", "status_code": 0, "data": None}

    if operation != "generateContent":
        return {"ok": False, "error": f"unknown op: {operation}", "status_code": 0, "data": None}

    model = payload.get("model") or settings.songwriter_llm_model or "gemini-2.5-flash"
    system_text = payload.get("system") or ""
    user_text = payload.get("user") or ""
    max_tokens = int(payload.get("max_tokens") or 1500)
    temperature = float(payload.get("temperature") or 0.7)

    base = settings.gemini_base_url.rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent?key={api_key}"

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",  # 작곡가는 JSON 응답 필요
        },
    }
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=body, headers={"Content-Type": "application/json"})

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}

    if r.is_success:
        error_msg = ""
    else:
        api_msg = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                api_msg = err.get("message", "")
            elif isinstance(err, str):
                api_msg = err
        error_msg = f"Gemini HTTP {r.status_code}{' — ' + str(api_msg)[:200] if api_msg else ''}"

    return {
        "ok": r.is_success,
        "status_code": r.status_code,
        "data": data,
        "error": error_msg,
    }


async def _call_telegram(
    db: Session, operation: str, payload: dict, settings, timeout: float
) -> dict[str, Any]:
    """Telegram Bot API.

    operations:
      - "sendMessage"    : 텍스트 메시지 전송
      - "sendAudio"      : 오디오 파일 전송 (Mureka audio_url을 그대로 넘기면 텔레그램이 가져감)
      - "sendDocument"   : 일반 파일 전송
      - "setWebhook"     : webhook URL 등록
      - "deleteWebhook"  : webhook 삭제
      - "getMe"          : 봇 정보 (검증용)
      - "getUpdates"     : 폴링 (웹훅 없을 때 디버깅용)

    chat_id가 payload에 없으면 settings.telegram_owner_chat_id로 자동 채움.
    """
    token = _resolve_secret(db, "telegram_bot_token", settings.telegram_bot_token)
    if not token:
        return {"ok": False, "error": "Telegram 봇 토큰 미설정 (TELEGRAM_BOT_TOKEN)", "status_code": 0, "data": None}

    base = settings.telegram_api_base.rstrip("/")
    url = f"{base}/bot{token}/{operation}"

    # chat_id 자동 채우기 (sendXxx 계열만)
    if operation.startswith("send") and payload and not payload.get("chat_id"):
        owner = _resolve_secret(db, "telegram_owner_chat_id", settings.telegram_owner_chat_id)
        if owner:
            payload = {**payload, "chat_id": owner}

    async with httpx.AsyncClient(timeout=timeout) as client:
        # GET (getMe, getUpdates) vs POST
        if operation in ("getMe", "getUpdates", "deleteWebhook"):
            r = await client.get(url, params=payload or None)
        else:
            r = await client.post(url, json=payload or {})

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}

    # 텔레그램은 ok 필드를 응답에 명시
    tg_ok = bool(isinstance(data, dict) and data.get("ok"))
    success = r.is_success and tg_ok

    if success:
        error_msg = ""
    else:
        api_msg = (data.get("description") if isinstance(data, dict) else "") or ""
        error_msg = f"Telegram HTTP {r.status_code}{' — ' + api_msg if api_msg else ''}"

    return {
        "ok": success,
        "status_code": r.status_code,
        "data": data,
        "error": error_msg,
    }
