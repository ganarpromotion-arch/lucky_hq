"""
텔레그램 직원 (telegram)

owner 채널로 곡을 보고하고 ✓/✗ 답장을 받는다.
모든 외부 호출은 api_manager.call_api("telegram", ...)를 통해서만.
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session

from .api_manager import call_api
from .models import Job, AuditLog, Agent, Batch


def _audit(db: Session, action: str, target: str = "", detail: dict | None = None,
           actor: str = "telegram") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail or {}))


def _set_agent_status(db: Session, slug: str, status: str) -> None:
    a = db.query(Agent).filter_by(slug=slug).first()
    if a:
        a.current_status = status
        a.last_seen_at = datetime.utcnow()


async def send_text(db: Session, text: str, parse_mode: str = "HTML") -> dict:
    """단순 텍스트 메시지."""
    return await call_api(
        db, provider="telegram", operation="sendMessage",
        payload={"text": text, "parse_mode": parse_mode, "disable_web_page_preview": True},
        requester="telegram",
    )


async def send_audio_for_job(db: Session, job: Job) -> dict:
    """곡 1개를 owner + 모든 활성 구독자에게 audio로 전송."""
    audio_url = (job.output or {}).get("audio_url")
    title = (job.input or {}).get("title", f"곡 #{job.id}")
    style = (job.input or {}).get("style", "")
    issue = (job.input or {}).get("issue", "")

    caption = (
        f"<b>#{job.id} · {title}</b>\n"
        f"{('<i>' + issue + '</i>' + chr(10)) if issue else ''}"
        f"🎼 {style}\n\n"
        f"채택은 이 메시지에 <b>✓</b> · 거절은 <b>✗</b> 로 답장"
    )

    from .config import get_settings
    from .models import TelegramSubscriber
    settings = get_settings()
    recipients: set[str] = set()
    if settings.telegram_owner_chat_id:
        recipients.add(str(settings.telegram_owner_chat_id))
    for s in db.query(TelegramSubscriber).filter_by(
        is_active=True, receives_song_reports=True
    ).all():
        recipients.add(str(s.chat_id))

    if not audio_url:
        # audio_url 없으면 텍스트만
        for cid in recipients:
            await send_text(db, caption + "\n\n⚠️ audio_url 없음")
        return {"ok": False, "error": "no audio_url"}

    last_result = {}
    owner_id = str(settings.telegram_owner_chat_id) if settings.telegram_owner_chat_id else None
    for cid in recipients:
        result = await call_api(
            db, provider="telegram", operation="sendAudio",
            payload={
                "chat_id": cid,
                "audio": audio_url,
                "caption": caption,
                "parse_mode": "HTML",
                "title": title,
                "performer": "Lucky HQ",
            },
            requester="telegram",
            timeout=90.0,
        )
        last_result = result
        # owner의 message_id만 Job에 저장 (✓/✗ 매칭용)
        if result.get("ok") and cid == owner_id:
            try:
                msg_id = ((result.get("data") or {}).get("result") or {}).get("message_id")
                if msg_id:
                    job.telegram_message_id = int(msg_id)
                    job.review_status = "pending_review"
                    _audit(db, "telegram.song_sent", target=f"job:{job.id}",
                           detail={"message_id": msg_id})
            except Exception:
                pass
    return last_result


async def send_video_file(db: Session, video_path: str, caption: str,
                          job_id: int | None = None) -> dict:
    """로컬 mp4 파일을 multipart로 텔레그램에 업로드.

    텔레그램 봇 API: multipart/form-data 직접 호출 (call_api는 JSON 전용).
    파일 50MB 제한.
    """
    import os
    import httpx
    from .config import get_settings

    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_owner_chat_id

    # DB Setting 우선 시도 (api_manager의 _resolve_secret과 동일 정책)
    from .models import Setting
    row = db.query(Setting).filter_by(key="telegram_bot_token").first()
    if row and row.value:
        token = row.value
    row = db.query(Setting).filter_by(key="telegram_owner_chat_id").first()
    if row and row.value:
        chat_id = row.value

    if not token or not chat_id:
        return {"ok": False, "error": "텔레그램 토큰/chat_id 미설정", "status_code": 0, "data": None}

    if not os.path.exists(video_path):
        return {"ok": False, "error": f"파일 없음: {video_path}", "status_code": 0, "data": None}

    file_size = os.path.getsize(video_path)
    if file_size > 49 * 1024 * 1024:  # 50MB 제한, 안전 마진
        return {
            "ok": False,
            "error": f"파일 너무 큼 ({file_size // 1024 // 1024}MB > 49MB 한도)",
            "status_code": 0, "data": None,
        }

    url = f"{settings.telegram_api_base}/bot{token}/sendVideo"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(video_path, "rb") as f:
                files = {"video": (os.path.basename(video_path), f, "video/mp4")}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                }
                r = await client.post(url, files=files, data=data)

        try:
            rdata = r.json()
        except Exception:
            rdata = {"raw": r.text[:500]}
        ok = r.is_success and bool(isinstance(rdata, dict) and rdata.get("ok"))

        # 감사 로그 (api_calls 테이블)
        from .models import ApiCall
        try:
            db.add(ApiCall(
                provider="telegram",
                operation="sendVideo",
                requester="telegram",
                status_code=r.status_code,
                ok=ok,
                duration_ms=0,
                request_summary={"file_size": file_size, "job_id": job_id},
                response_summary={"message_id": ((rdata.get("result") or {}) if isinstance(rdata, dict) else {}).get("message_id")},
            ))
            db.commit()
        except Exception:
            db.rollback()

        if ok:
            return {"ok": True, "status_code": r.status_code, "data": rdata, "error": ""}
        else:
            api_msg = (rdata.get("description") if isinstance(rdata, dict) else "") or ""
            return {
                "ok": False,
                "status_code": r.status_code,
                "data": rdata,
                "error": f"Telegram HTTP {r.status_code}{' — ' + api_msg if api_msg else ''}",
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "status_code": 0, "data": None}


async def report_batch(db: Session, batch: Batch) -> dict:
    """배치 시작 시 / 완료 시 모든 활성 멤버에게 요약 메시지.

    owner + receives_song_reports=True인 멤버 모두에게 발송.
    """
    jobs = db.query(Job).filter_by(batch_id=batch.id).order_by(Job.id).all()
    done = [j for j in jobs if j.status == "done"]
    failed = [j for j in jobs if j.status == "failed"]

    lines = [
        f"🍀 <b>음악 배치 #{batch.id} 보고</b>",
        f"트리거: {batch.trigger}",
        f"성공: <b>{len(done)}</b>개  · 실패: {len(failed)}개  (목표 {batch.target_count}개)",
        "",
    ]
    for j in done:
        title = (j.input or {}).get("title", f"#{j.id}")
        lines.append(f"  ✅ #{j.id} {title}")
    for j in failed:
        title = (j.input or {}).get("title", f"#{j.id}")
        err = (j.error or "")[:60]
        lines.append(f"  ❌ #{j.id} {title} — {err}")

    if done:
        lines.append("")
        lines.append("아래 곡들을 하나씩 보내드립니다. ✓ / ✗ 로 답장해주세요.")

    text = "\n".join(lines)

    # 수신 대상: env owner + receives_song_reports=True인 활성 구독자
    from .config import get_settings
    settings = get_settings()
    recipients: set[str] = set()
    if settings.telegram_owner_chat_id:
        recipients.add(str(settings.telegram_owner_chat_id))
    from .models import TelegramSubscriber
    subs = db.query(TelegramSubscriber).filter_by(
        is_active=True, receives_song_reports=True
    ).all()
    for s in subs:
        recipients.add(str(s.chat_id))

    last_result = {}
    for cid in recipients:
        last_result = await call_api(
            db, provider="telegram", operation="sendMessage",
            payload={"chat_id": cid, "text": text, "parse_mode": "HTML",
                     "disable_web_page_preview": True},
            requester="telegram",
        )
    return last_result