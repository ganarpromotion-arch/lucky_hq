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
    """곡 1개를 owner에게 audio 메시지로 전송.
    텔레그램이 audio_url을 직접 가져가서 재생 가능한 형태로 변환해줌."""
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

    if not audio_url:
        # 오디오 URL 없으면 텍스트만이라도 보냄
        return await send_text(db, caption + "\n\n⚠️ audio_url 없음")

    result = await call_api(
        db, provider="telegram", operation="sendAudio",
        payload={
            "audio": audio_url,
            "caption": caption,
            "parse_mode": "HTML",
            "title": title,
            "performer": "Lucky HQ",
        },
        requester="telegram",
        timeout=90.0,  # 텔레그램이 audio_url 가져와야 해서 시간 좀 걸림
    )
    if result.get("ok"):
        try:
            msg_id = ((result.get("data") or {}).get("result") or {}).get("message_id")
            if msg_id:
                job.telegram_message_id = int(msg_id)
                job.review_status = "pending_review"
                _audit(db, "telegram.song_sent", target=f"job:{job.id}",
                       detail={"message_id": msg_id})
        except Exception:
            pass
    return result


async def report_batch(db: Session, batch: Batch) -> dict:
    """배치 시작 시 / 완료 시 owner에게 요약 메시지."""
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

    return await send_text(db, "\n".join(lines))