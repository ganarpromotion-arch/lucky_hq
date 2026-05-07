"""
텔레그램 webhook 라우터

- GET  /api/telegram/health      : 봇 토큰 검증 (getMe)
- POST /api/telegram/webhook     : Telegram → 본부 로 들어오는 업데이트

분기:
  1. /join CODE   → 가입 코드로 신규 멤버 등록
  2. /whoami      → 현재 사용자 역할
  3. /help        → 명령 안내
  4. 답장 + ✓/✗   → 곡 채택/거절
  5. 일반 메시지  → 지휘관 챗봇 (등록 멤버만)
"""
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..config import get_settings
from ..models import AuditLog, Job, TelegramSubscriber, TelegramJoinCode
from ..api_manager import call_api

log = logging.getLogger("lucky_hq.telegram")

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


def _is_owner(chat_id: str | int, settings) -> bool:
    owner_id = settings.telegram_owner_chat_id
    return bool(owner_id) and str(chat_id) == str(owner_id)


def _get_subscriber(db: Session, chat_id: str | int) -> TelegramSubscriber | None:
    return db.query(TelegramSubscriber).filter_by(chat_id=str(chat_id)).first()


def _can_chat(db: Session, chat_id: str | int, settings) -> tuple[bool, str]:
    """챗봇 응답 자격 확인. (허용, role)"""
    if _is_owner(chat_id, settings):
        return True, "owner"
    sub = _get_subscriber(db, chat_id)
    if sub and sub.is_active and sub.receives_chat_replies:
        return True, sub.role
    return False, ""


@router.get("/health")
async def telegram_health(db: Session = Depends(get_db)):
    result = await call_api(db, provider="telegram", operation="getMe",
                            payload={}, requester="owner")
    return {
        "ok": result.get("ok"),
        "bot": (result.get("data") or {}).get("result") if result.get("ok") else None,
        "error": result.get("error") or "",
    }


@router.post("/send-test")
async def telegram_send_test(db: Session = Depends(get_db)):
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


async def _send_back(db: Session, chat_id: str | int, text: str) -> None:
    """webhook 응답으로 메시지 보내기."""
    await call_api(
        db, provider="telegram", operation="sendMessage",
        payload={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        requester="commander",
    )


async def _handle_join(db: Session, code: str, chat_id: str | int, from_user: dict) -> str:
    """/join CODE 처리."""
    code = code.strip().upper()
    row = db.query(TelegramJoinCode).filter_by(code=code).first()
    if not row:
        return "⚠ 유효하지 않은 코드입니다."
    if row.used:
        return "⚠ 이미 사용된 코드입니다."
    if row.expires_at and row.expires_at < datetime.utcnow():
        return "⚠ 만료된 코드입니다. owner에게 새 코드를 요청하세요."

    # 이미 가입된 사용자?
    existing = _get_subscriber(db, chat_id)
    if existing:
        return f"이미 가입되어 있습니다 (역할: {existing.role}). 코드는 사용되지 않았어요."

    # 가입
    sub = TelegramSubscriber(
        chat_id=str(chat_id),
        role=row.role,
        nickname=from_user.get("first_name", ""),
        username=from_user.get("username", ""),
        first_name=from_user.get("first_name", ""),
        is_active=True,
    )
    db.add(sub)
    row.used = True
    row.used_by_chat_id = str(chat_id)
    row.used_at = datetime.utcnow()
    db.add(AuditLog(actor="owner", action="team.member_joined",
                    target=f"chat:{chat_id}",
                    detail={"role": row.role, "code": code,
                            "username": from_user.get("username", "")}))
    db.commit()

    role_label = {"owner": "오너", "manager": "최고 팀장", "operator": "운영자",
                  "approver": "승인자", "viewer": "뷰어", "guest": "게스트"}.get(row.role, row.role)
    return (
        f"🍀 <b>Lucky HQ 가입 완료</b>\n"
        f"역할: <b>{role_label}</b>\n\n"
        f"이제 본부와 자유롭게 대화할 수 있습니다.\n"
        f"명령:\n"
        f"  /whoami — 내 정보\n"
        f"  /help — 도움말"
    )


def _whoami_text(db: Session, chat_id: str | int, settings) -> str:
    if _is_owner(chat_id, settings):
        return f"<b>역할: 오너</b>\nchat_id: <code>{chat_id}</code>\n모든 권한"
    sub = _get_subscriber(db, chat_id)
    if not sub:
        return "등록되지 않은 사용자입니다. owner에게 가입 코드를 요청하세요."
    role_label = {"manager": "최고 팀장", "operator": "운영자", "approver": "승인자",
                  "viewer": "뷰어", "guest": "게스트"}.get(sub.role, sub.role)
    return (f"<b>역할: {role_label}</b>\n"
            f"이름: {sub.nickname or sub.first_name}\n"
            f"chat_id: <code>{chat_id}</code>")


HELP_TEXT = (
    "🍀 <b>Lucky HQ 명령</b>\n\n"
    "<b>/whoami</b> — 내 역할 확인\n"
    "<b>/join CODE</b> — 가입 코드로 등록\n"
    "<b>/help</b> — 이 도움말\n\n"
    "그 외엔 자유롭게 질문하시면 지휘관(AI)이 답변합니다.\n"
    "곡에 답장으로 <b>✓</b> 또는 <b>✗</b> 보내면 채택/거절됩니다."
)


@router.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db),
                           x_telegram_bot_api_secret_token: str | None = Header(default=None)):
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

    if not chat_id or not text:
        db.add(AuditLog(actor="telegram", action="webhook.received",
                        detail={"text": text[:80]}))
        db.commit()
        return {"ok": True}

    # 마지막 활동 기록 (등록된 멤버라면)
    sub = _get_subscriber(db, chat_id)
    if sub:
        sub.last_seen_at = datetime.utcnow()
        db.commit()

    # ── 명령 처리 ──
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()  # /join@botname → /join
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/start":
            reply = (f"🍀 <b>Lucky HQ에 오신 걸 환영합니다</b>\n\n"
                     f"가입하려면: <code>/join CODE</code>\n"
                     f"명령 보기: <code>/help</code>")
            await _send_back(db, chat_id, reply)
            return {"ok": True}

        if cmd == "/help":
            await _send_back(db, chat_id, HELP_TEXT)
            return {"ok": True}

        if cmd == "/whoami":
            await _send_back(db, chat_id, _whoami_text(db, chat_id, settings))
            return {"ok": True}

        if cmd == "/join":
            if not arg:
                await _send_back(db, chat_id, "사용법: <code>/join CODE</code>")
                return {"ok": True}
            reply = await _handle_join(db, arg, chat_id, from_user)
            await _send_back(db, chat_id, reply)
            return {"ok": True}

        await _send_back(db, chat_id, f"알 수 없는 명령: {cmd}\n<code>/help</code> 확인하세요.")
        return {"ok": True}

    # ── 답장 처리 ──
    if reply_to_msg_id:
        # 1) 일일 큐레이터 안에 대한 답장? (1-3-2 / 패스)
        from ..daily_curator import get_active_proposal, parse_response, handle_choice
        active = get_active_proposal(db)
        if active:
            # 답장 대상이 큐레이터 안 메시지 중 하나인지 확인
            our_msg_ids = [m.get("message_id") for m in (active.telegram_message_ids or [])]
            if int(reply_to_msg_id) in our_msg_ids:
                # 권한 체크: owner 또는 manager만
                ok_role, role = _can_chat(db, chat_id, settings)
                if role not in ("owner", "manager"):
                    await _send_back(db, chat_id, "⚠ 큐레이터 안 응답은 owner 또는 최고 팀장만 가능합니다.")
                    return {"ok": True}
                parsed = parse_response(text)
                if parsed is None:
                    await _send_back(
                        db, chat_id,
                        "응답 형식: <code>1-3-2</code> 또는 <code>1-3-2 x6</code> 또는 <code>패스</code>"
                    )
                    return {"ok": True}
                reply = await handle_choice(db, active, parsed, chat_id, role)
                await _send_back(db, chat_id, reply)
                return {"ok": True}

        # 2) 곡에 대한 ✓/✗ 답장
        ok_role, role = _can_chat(db, chat_id, settings)
        can_review = (role in ("owner", "manager", "approver"))
        approve = any(t in text for t in ("✓", "✔", "채택", "ok", "OK", "좋아", "좋음", "yes", "Yes", "YES"))
        if not approve and text in ("o", "O", "ㅇ"):
            approve = True
        reject = any(t in text for t in ("✗", "✘", "거절", "no", "No", "NO", "패스", "skip", "Skip"))
        if not reject and text in ("x", "X", "ㄴ"):
            reject = True

        job = db.query(Job).filter_by(telegram_message_id=int(reply_to_msg_id)).first()
        if job and (approve or reject):
            if not can_review:
                await _send_back(db, chat_id, "⚠ 곡 채택 권한이 없습니다 (owner/팀장/승인자만).")
                return {"ok": True}
            actor = "owner" if role == "owner" else f"{role}:{chat_id}"
            if approve:
                job.review_status = "approved"
                db.add(AuditLog(actor=actor, action="music.approved",
                                target=f"job:{job.id}", detail={"text": text[:50]}))
            else:
                job.review_status = "rejected"
                db.add(AuditLog(actor=actor, action="music.rejected",
                                target=f"job:{job.id}", detail={"text": text[:50]}))
            db.commit()
            return {"ok": True}

    # ── 일반 텍스트 → 지휘관 챗봇 ──
    allowed, role = _can_chat(db, chat_id, settings)
    if not allowed:
        # 등록 안 된 사용자
        db.add(AuditLog(actor="telegram", action="chat.ignored_unregistered",
                        detail={"chat_id": str(chat_id),
                                "user": (from_user.get("username") or from_user.get("first_name", "")),
                                "text": text[:60]}))
        db.commit()
        # 가입 안내만 한 번
        await _send_back(db, chat_id,
                         "🍀 등록되지 않은 사용자입니다.\n"
                         "owner에게 가입 코드를 받아 <code>/join CODE</code> 보내세요.")
        return {"ok": True}

    try:
        from ..commander import handle_message
        reply = await handle_message(db, text, chat_id, from_user)
        await _send_back(db, chat_id, reply)
    except Exception as e:
        log.exception("commander reply failed")
        db.add(AuditLog(actor="commander", action="chat.error",
                        detail={"error": str(e)[:200]}))
        db.commit()

    return {"ok": True}


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
