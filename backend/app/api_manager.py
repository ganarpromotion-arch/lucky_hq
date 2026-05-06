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
