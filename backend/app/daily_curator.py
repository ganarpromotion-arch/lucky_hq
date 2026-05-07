"""
일일 큐레이터 직원

매일 아침 8시 KST:
  1. 큐레이터가 Gemini로 오늘의 5x3 안 작성
  2. owner + 모든 활성 매니저/구독자에게 텔레그램 발송
  3. 응답 형식 안내: "1-3-2" 또는 "1-3-2 x6"

응답 처리 (telegram webhook에서 호출):
  4. "1-3-2" 또는 "1-3-2 x6" 패턴 파싱
  5. 큐레이터 옵션과 매핑하여 issue 문자열 생성
  6. 배치 트리거 (target_count=6, make_video=True 기본)
"""
from __future__ import annotations
import logging
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .api_manager import call_api
from .config import get_settings
from .curator import propose_options, combine_choice
from .models import DailyProposal, TelegramSubscriber, AuditLog, Batch

log = logging.getLogger("lucky_hq.daily_curator")


def _kst_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _format_proposal_message(proposal: DailyProposal) -> str:
    """텔레그램으로 보낼 5x3 안 메시지."""
    lines = [
        f"🍀 <b>오늘의 큐레이터 안 ({proposal.date_kst})</b>",
        "",
        "<b>🌐 언어 / 톤</b>",
    ]
    for i, item in enumerate(proposal.languages, 1):
        lines.append(f"  {i}. {item}")
    lines.extend(["", "<b>💫 분위기</b>"])
    for i, item in enumerate(proposal.moods, 1):
        lines.append(f"  {i}. {item}")
    lines.extend(["", "<b>🏷️ 키워드</b>"])
    for i, item in enumerate(proposal.keywords, 1):
        lines.append(f"  {i}. {item}")
    lines.extend([
        "",
        "<b>응답 방법</b> (이 메시지에 답장):",
        "  <code>1-3-2</code>     → 1곡 (영상 포함)",
        "  <code>1-3-2 x6</code>  → 6곡 (영상 포함)",
        "  <code>패스</code>       → 오늘은 안 만듦",
    ])
    return "\n".join(lines)


# 응답 패턴: "1-3-2" 또는 "1 - 3 - 2" 또는 "1-3-2 x6" 또는 "1-3-2 6곡"
_PATTERN = re.compile(
    r"^\s*([1-5])\s*[-./, ]\s*([1-5])\s*[-./, ]\s*([1-5])"
    r"(?:\s*[xX×]?\s*(\d+)\s*곡?)?\s*$"
)
_SKIP_KEYWORDS = ("패스", "스킵", "skip", "Skip", "SKIP", "pass", "Pass", "취소", "cancel")


def parse_response(text: str) -> Optional[dict]:
    """owner 응답 파싱.

    Returns:
      None: 매칭 안 됨 (다른 메시지로 처리)
      {"action": "skip"}: 패스
      {"action": "choose", "lang": 1-5, "mood": 1-5, "keyword": 1-5, "count": int}: 선택
    """
    if not text:
        return None
    t = text.strip()

    # 패스
    for kw in _SKIP_KEYWORDS:
        if t == kw or t.startswith(kw):
            return {"action": "skip"}

    m = _PATTERN.match(t)
    if not m:
        return None

    lang_idx = int(m.group(1))
    mood_idx = int(m.group(2))
    keyword_idx = int(m.group(3))
    count = int(m.group(4)) if m.group(4) else 1
    count = max(1, min(count, 10))  # 1~10

    return {
        "action": "choose",
        "lang": lang_idx,
        "mood": mood_idx,
        "keyword": keyword_idx,
        "count": count,
    }


def get_active_proposal(db: Session) -> Optional[DailyProposal]:
    """오늘의 waiting 상태 proposal."""
    return (
        db.query(DailyProposal)
        .filter_by(date_kst=_kst_today(), status="waiting")
        .order_by(DailyProposal.id.desc())
        .first()
    )


async def make_and_send_today_proposal(db: Session) -> dict:
    """오늘의 5x3 안을 만들어서 모든 멤버에게 발송.
    같은 날에 이미 발송된 게 있으면 패스 (중복 방지)."""
    today = _kst_today()
    existing = db.query(DailyProposal).filter_by(date_kst=today).first()
    if existing:
        log.info(f"오늘({today}) proposal 이미 있음 (#{existing.id}), 새로 만들지 않음")
        return {"ok": False, "reason": "already_exists", "proposal_id": existing.id}

    # 1) 큐레이터 호출
    options = await propose_options(db)
    proposal = DailyProposal(
        date_kst=today,
        languages=options.get("languages", []),
        moods=options.get("moods", []),
        keywords=options.get("keywords", []),
        status="waiting",
    )
    db.add(proposal)
    db.flush()
    db.add(AuditLog(
        actor="daily_curator",
        action="proposal.created",
        target=f"proposal:{proposal.id}",
        detail={"source": options.get("source"), "date": today},
    ))
    db.commit()
    db.refresh(proposal)

    # 2) 텔레그램 발송 (owner + 활성 멤버)
    settings = get_settings()
    text = _format_proposal_message(proposal)
    recipients: list[str] = []
    if settings.telegram_owner_chat_id:
        recipients.append(str(settings.telegram_owner_chat_id))
    for s in db.query(TelegramSubscriber).filter_by(
        is_active=True, receives_song_reports=True
    ).all():
        recipients.append(str(s.chat_id))

    sent_msg_ids: list[dict] = []
    for cid in recipients:
        result = await call_api(
            db, provider="telegram", operation="sendMessage",
            payload={
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            requester="daily_curator",
            timeout=30.0,
        )
        if result.get("ok"):
            try:
                msg_id = ((result.get("data") or {}).get("result") or {}).get("message_id")
                if msg_id:
                    sent_msg_ids.append({"chat_id": cid, "message_id": int(msg_id)})
            except Exception:
                pass

    proposal.telegram_message_ids = sent_msg_ids
    proposal.sent_at = datetime.utcnow()
    db.add(AuditLog(
        actor="daily_curator",
        action="proposal.sent",
        target=f"proposal:{proposal.id}",
        detail={"recipients": len(recipients), "sent_ok": len(sent_msg_ids)},
    ))
    db.commit()
    return {"ok": True, "proposal_id": proposal.id, "sent_to": len(sent_msg_ids)}


async def handle_choice(db: Session, proposal: DailyProposal, choice: dict,
                        chat_id: str | int, sender_role: str) -> str:
    """owner/매니저의 선택 처리. 배치 트리거하고 응답 메시지 반환."""
    if choice["action"] == "skip":
        proposal.status = "skipped"
        proposal.chosen_at = datetime.utcnow()
        proposal.chosen_by_chat_id = str(chat_id)
        db.add(AuditLog(actor=f"{sender_role}:{chat_id}",
                        action="proposal.skipped",
                        target=f"proposal:{proposal.id}"))
        db.commit()
        return "🍀 오늘은 곡을 만들지 않습니다. 내일 아침 새로운 안을 보내드릴게요."

    # 선택
    li = choice["lang"] - 1
    mi = choice["mood"] - 1
    ki = choice["keyword"] - 1
    n = choice["count"]

    if not (0 <= li < len(proposal.languages)
            and 0 <= mi < len(proposal.moods)
            and 0 <= ki < len(proposal.keywords)):
        return "⚠ 옵션 번호가 잘못됐어요. 1~5 사이여야 합니다."

    language = proposal.languages[li]
    mood = proposal.moods[mi]
    keyword = proposal.keywords[ki]
    issue = combine_choice(language, mood, keyword)

    # 동시 배치 검사
    busy = db.query(Batch).filter(
        Batch.department_slug == "music",
        Batch.status.in_(("pending", "running", "reporting")),
    ).first()
    if busy:
        return f"⚠ 이미 진행 중인 배치가 있습니다 (#{busy.id}). 끝나면 다시 골라주세요."

    # 배치 생성 — 모든 곡이 같은 issue 받음 (큐레이터가 골라준 그 조합)
    batch = Batch(
        department_slug="music",
        kind=f"daily_curator_{n}{'_v' if n > 0 else ''}",
        trigger=f"daily_curator:{proposal.id}",
        status="pending",
        target_count=n,
        issues=[issue] * n,  # 같은 조합으로 N곡
        make_video=True,     # 영상까지 자동 생성
    )
    db.add(batch)
    db.flush()
    db.add(AuditLog(
        actor=f"{sender_role}:{chat_id}",
        action="batch.created",
        target=f"batch:{batch.id}",
        detail={"trigger": "daily_curator", "proposal_id": proposal.id,
                "language": language, "mood": mood, "keyword": keyword, "count": n},
    ))
    proposal.status = "chosen"
    proposal.chosen_language_idx = li
    proposal.chosen_mood_idx = mi
    proposal.chosen_keyword_idx = ki
    proposal.chosen_by_chat_id = str(chat_id)
    proposal.chosen_at = datetime.utcnow()
    proposal.triggered_batch_id = batch.id
    db.commit()
    db.refresh(batch)

    # 백그라운드 워커 트리거
    import asyncio
    from .batch_worker import run_batch
    asyncio.create_task(run_batch(batch.id))

    return (
        f"🍀 <b>좋아요!</b>\n"
        f"  키워드: {keyword}\n"
        f"  분위기: {mood}\n"
        f"  언어: {language}\n"
        f"  → <b>{n}곡 + 영상</b> 만들기 시작합니다 (배치 #{batch.id})\n\n"
        f"  완성되면 곡 하나씩 보내드릴게요. ✓ / ✗ 로 답장하세요."
    )
