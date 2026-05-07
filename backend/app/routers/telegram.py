"""
텔레그램 webhook 라우터

- GET  /api/telegram/health      : 봇 토큰 검증 (getMe)
- POST /api/telegram/webhook     : Telegram → 본부 로 들어오는 업데이트 (답장 등)

V1.x: webhook은 자리만 만들어두고 ✓/✗ 답장 처리는 다음 단계에서 본격 동작.
지금은 답장이 들어오면 audit_logs에만 기록.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..config import get_settings
from ..models import AuditLog, Job
from ..api_manager import call_api

log = logging.getLogger("lucky_hq.telegram")

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.get("/health")
async def telegram_health(db: Session = Depends(get_db)):
    """봇 토큰 + 권한 검증."""
    result = await call_api(db, provider="telegram", operation="getMe",
                            payload={}, requester="owner")
    return {
        "ok": result.get("ok"),
        "bot": (result.get("data") or {}).get("result") if result.get("ok") else None,
        "error": result.get("error") or "",
    }


@router.post("/send-test")
async def telegram_send_test(db: Session = Depends(get_db)):
    """owner에게 테스트 메시지 1통 — 환경변수 검증용."""
    text = "🍀 <b>Lucky HQ 연결 확인</b>\n본부에서 보내는 테스트 메시지입니다."
    result = await call_api(
        db, provider="telegram", operation="sendMessage",
        payload={"text": text, "parse_mode": "HTML"},
        requester="owner",
    )
    db.add(AuditLog(actor="owner", action="telegram.test_message",
                    detail={"ok": result.get("ok")}))
    db.commit()
    return result


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db),
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    """텔레그램이 보내는 업데이트 수신.

    분기:
      1. 답장(reply_to_message) + ✓/✗ → 곡 채택/거절
      2. 답장 + 다른 텍스트 → audit만
      3. 그 외 메시지 → 지휘관 챗봇 (Step C)

    보안: secret token 헤더 검증 + (Step C에선) owner chat_id만 챗봇 응답
    """
    settings = get_settings()
    expected = settings.telegram_webhook_secret

    if not expected:
        raise HTTPException(503, "TELEGRAM_WEBHOOK_SECRET 미설정")
    if x_telegram_bot_api_secret_token != expected:
        raise HTTPException(403, "invalid secret token")

    update = await request.json()
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    from_user = msg.get("from") or {}
    reply_to = msg.get("reply_to_message") or {}
    reply_to_msg_id = reply_to.get("message_id")

    # 1) 곡에 대한 ✓/✗ 답장
    if reply_to_msg_id and text:
        approve = any(t in text for t in ("✓", "✔", "채택", "ok", "OK", "좋아", "좋음", "yes", "Yes", "YES"))
        if not approve and text in ("o", "O", "ㅇ"):
            approve = True
        reject = any(t in text for t in ("✗", "✘", "거절", "no", "No", "NO", "패스", "skip", "Skip"))
        if not reject and text in ("x", "X", "ㄴ"):
            reject = True

        job = db.query(Job).filter_by(telegram_message_id=int(reply_to_msg_id)).first()
        if job:
            if approve and not reject:
                job.review_status = "approved"
                db.add(AuditLog(actor="owner", action="music.approved",
                                target=f"job:{job.id}", detail={"text": text[:50]}))
            elif reject and not approve:
                job.review_status = "rejected"
                db.add(AuditLog(actor="owner", action="music.rejected",
                                target=f"job:{job.id}", detail={"text": text[:50]}))
            else:
                db.add(AuditLog(actor="owner", action="music.review_unclear",
                                target=f"job:{job.id}", detail={"text": text[:50]}))
            db.commit()
            return {"ok": True}
        else:
            db.add(AuditLog(actor="telegram", action="webhook.reply_unmatched",
                            detail={"reply_to": reply_to_msg_id, "text": text[:50]}))
            db.commit()

    # 2) 일반 메시지 → 지휘관 챗봇
    # Step C: owner chat_id만 응답 (악용 방지)
    if text and chat_id is not None:
        from ..commander import handle_message
        from ..api_manager import call_api
        owner_id = settings.telegram_owner_chat_id
        if owner_id and str(chat_id) != str(owner_id):
            # 등록되지 않은 사용자 — 무시 + audit
            db.add(AuditLog(actor="telegram", action="chat.ignored_unregistered",
                            detail={"chat_id": str(chat_id),
                                    "user": (from_user.get("username") or from_user.get("first_name", "")),
                                    "text": text[:60]}))
            db.commit()
            return {"ok": True}

        # owner 메시지 → 지휘관 호출 + 답장
        try:
            reply = await handle_message(db, text, chat_id, from_user)
            await call_api(
                db, provider="telegram", operation="sendMessage",
                payload={"chat_id": chat_id, "text": reply, "parse_mode": "HTML"},
                requester="commander",
            )
        except Exception as e:
            log.exception("commander reply failed")
            db.add(AuditLog(actor="commander", action="chat.error",
                            detail={"error": str(e)[:200]}))
            db.commit()
        return {"ok": True}

    db.add(AuditLog(actor="telegram", action="webhook.received",
                    detail={"text": text[:80], "chat_id": str(chat_id)}))
    db.commit()
    return {"ok": True}
