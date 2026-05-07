"""
지휘관 직원 (commander)

텔레그램 webhook으로 들어온 자유 텍스트 메시지를 받아 Gemini로 답한다.

V1: owner만 응답 (chat_id 검증)
V2(D 단계): 모든 등록된 구독자 응답

미래의 지휘관 책임 (메모리 명세):
  - 의도 분류 (자연어 → 직원 호출)
  - 안전 게이트 (forbidden / approval / lock / budget / mode)
  - 직원 분배

V1은 단순 챗봇 + 본부 상태 질문 답변 정도로 시작.
"""
from __future__ import annotations
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from .api_manager import call_api
from .models import Job, Batch, Agent, AuditLog

log = logging.getLogger("lucky_hq.commander")


SYSTEM_PROMPT = """너는 'Lucky HQ' AI 운영본부의 지휘관(Commander)이야.
owner와 텔레그램으로 대화하며 본부 상태를 보고하고 간단한 질문에 답한다.

역할:
- owner의 자유 질문에 친근하고 짧게 답변 (한국어, 1~3문장)
- 본부 컨텍스트(아래 제공됨)를 참고
- 위험 작업 요청(곡 생성, 삭제 등)은 "그건 음악부서 페이지에서 직접 해주세요" 식으로 안내
- 모르는 건 모른다고 솔직히

말투: 차분하고 신뢰감 있게. 이모지는 거의 안 씀."""


def _build_context(db: Session) -> str:
    """현재 본부 상태 한 줄 요약 (Gemini system 컨텍스트용)."""
    try:
        active_agents = db.query(Agent).filter_by(is_active=True).count()
        running_batches = db.query(Batch).filter(Batch.status.in_(("pending", "running", "reporting"))).count()
        archived_songs = db.query(Job).filter(Job.local_audio_path != "", Job.deleted_at.is_(None)).count()
        approved = db.query(Job).filter_by(review_status="approved").count()
        pending = db.query(Job).filter_by(review_status="pending_review").count()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return (
            f"[본부 컨텍스트 — {now} KST]\n"
            f"- 활성 직원: {active_agents}명\n"
            f"- 진행 중 배치: {running_batches}건\n"
            f"- 보관 곡: {archived_songs}개 (채택 {approved}, 검토 대기 {pending})\n"
        )
    except Exception:
        return "[본부 컨텍스트 불가]"


async def reply_to_text(db: Session, text: str, chat_id: int | str | None = None) -> str:
    """owner 메시지에 대한 답변 텍스트 생성. 실패 시 안전한 폴백."""
    text = (text or "").strip()
    if not text:
        return "메시지가 비어있어요."

    # 1) 본부 상태 빠른 답변 (LLM 호출 없이 — 자주 묻는 질문)
    lo = text.lower()
    if any(k in lo for k in ("status", "상태", "어떻게", "현황")) and len(text) < 30:
        return _build_context(db).replace("[본부 컨텍스트 — ", "본부 상태 (").replace("]", ")")

    # 2) Gemini 호출
    context = _build_context(db)
    payload = {
        "model": "gemini-2.5-flash",
        "system": f"{SYSTEM_PROMPT}\n\n{context}",
        "user": text,
        "max_tokens": 500,
        "temperature": 0.7,
    }
    # generateContent는 JSON mode 기본값이라 평문 응답 받으려면 별도 처리
    # _call_gemini가 JSON mode 강제하지만, 평문 응답을 받기 위해 mime 우회
    # → 임시로 system에 "JSON 금지, 평문으로만 답변" 추가
    payload["system"] = f"{payload['system']}\n\n출력은 JSON이 아니라 자연스러운 평문 한국어로만."

    result = await call_api(db, provider="gemini", operation="generateContent",
                            payload=payload, requester="commander", timeout=30.0)
    if not result.get("ok"):
        log.warning(f"commander gemini failed: {result.get('error')}")
        return "지금은 답변하기 어려워요. 잠시 후 다시 시도해주세요."

    data = result.get("data") or {}
    candidates = data.get("candidates") or []
    if not candidates:
        return "응답이 비어있네요. 다시 한번 물어봐주세요."

    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    for p in parts:
        if isinstance(p, dict) and p.get("text"):
            reply = p["text"].strip()
            # JSON 흔적 제거 (LLM이 가끔 {"reply": "..."} 같이 반환)
            if reply.startswith("{") and "}" in reply:
                import json as _json
                try:
                    obj = _json.loads(reply)
                    if isinstance(obj, dict):
                        reply = obj.get("reply") or obj.get("answer") or obj.get("text") or reply
                except Exception:
                    pass
            return reply[:1500]

    return "응답을 이해하지 못했어요."


async def handle_message(db: Session, text: str, chat_id: int | str,
                         from_user: dict | None = None) -> str:
    """webhook에서 호출. 챗봇 답변 생성 + 감사 기록."""
    user_name = ""
    if from_user:
        user_name = from_user.get("first_name") or from_user.get("username") or ""

    db.add(AuditLog(
        actor="commander",
        action="chat.received",
        detail={"chat_id": str(chat_id), "user": user_name, "text_len": len(text)},
    ))
    db.commit()

    reply = await reply_to_text(db, text, chat_id=chat_id)

    db.add(AuditLog(
        actor="commander",
        action="chat.replied",
        detail={"chat_id": str(chat_id), "reply_len": len(reply)},
    ))
    db.commit()
    return reply
