"""
배치 워커

전체 흐름:
  1. 큐레이터 → 이슈 N개 픽
  2. 각 이슈마다:
     a. 작곡가가 기획안 생성 (LLM 또는 룰)
     b. 음악 프로듀서가 Mureka에 의뢰
     c. Mureka 폴링 (작업 완료까지)
     d. (옵션) 영상 편집자가 mp4 인코딩
     e. 텔레그램으로 곡 audio + mp4 전송
  3. 배치 완료 보고

순차 처리 (Mureka rate limit 회피).
배치 옵션:
  - make_video=True: 곡마다 mp4 생성 후 전송
  - make_video=False: audio만 전송 (기본, 빠름)
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Batch, Job, AuditLog, Agent, Video
from .api_manager import call_api
from .songwriter import compose_plan as songwriter_compose
from . import telegram_agent
from . import video_maker
from . import archiver

log = logging.getLogger("lucky_hq.batch")

# Mureka 폴링 설정
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 240   # 1곡당 최대 4분 대기
INTER_SONG_DELAY_SEC = 3  # 곡과 곡 사이 간격 (rate limit 안전)


def _audit(db: Session, action: str, target: str = "", detail: dict | None = None,
           actor: str = "music_producer") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail or {}))


def _set_agent(db: Session, slug: str, status: str) -> None:
    a = db.query(Agent).filter_by(slug=slug).first()
    if a:
        a.current_status = status
        a.last_seen_at = datetime.utcnow()


async def run_batch(batch_id: int) -> None:
    """배치 1건 끝까지 처리. 자체 DB 세션 사용 (BackgroundTask이므로)."""
    db = SessionLocal()
    try:
        batch = db.get(Batch, batch_id)
        if not batch:
            log.error(f"batch {batch_id} not found")
            return

        batch.status = "running"
        batch.started_at = datetime.utcnow()
        _audit(db, "batch.started", target=f"batch:{batch_id}",
               detail={"target_count": batch.target_count, "trigger": batch.trigger})
        db.commit()

        issues: list[str] = list(batch.issues or [])

        for idx, issue in enumerate(issues, start=1):
            try:
                await _process_one(db, batch, issue, idx)
            except Exception as e:
                log.exception(f"batch {batch_id} song {idx} failed")
                _audit(db, "batch.song_error", target=f"batch:{batch_id}",
                       detail={"idx": idx, "error": str(e)[:200]})
                db.commit()

            # 다음 곡 전 잠시 휴식
            if idx < len(issues):
                await asyncio.sleep(INTER_SONG_DELAY_SEC)

        # 완료 처리
        db.refresh(batch)
        completed = db.query(Job).filter_by(batch_id=batch_id, status="done").count()
        failed = db.query(Job).filter_by(batch_id=batch_id, status="failed").count()
        batch.completed_count = completed
        batch.failed_count = failed
        batch.status = "reporting"
        batch.finished_at = datetime.utcnow()
        _audit(db, "batch.compute_done", target=f"batch:{batch_id}",
               detail={"completed": completed, "failed": failed})
        db.commit()

        # 텔레그램 보고
        _set_agent(db, "telegram", "보고 중")
        db.commit()
        try:
            await telegram_agent.report_batch(db, batch)
            db.commit()

            # 성공한 곡들: 영상 만들지 audio만 보낼지 결정
            done_jobs = db.query(Job).filter_by(batch_id=batch_id, status="done").order_by(Job.id).all()
            for job in done_jobs:
                try:
                    if batch.make_video:
                        await _make_and_send_video(db, batch, job)
                    else:
                        await telegram_agent.send_audio_for_job(db, job)
                    db.commit()
                except Exception as e:
                    log.exception(f"telegram send failed for job {job.id}")
                    _audit(db, "telegram.send_error", target=f"job:{job.id}",
                           detail={"error": str(e)[:200]}, actor="telegram")
                    db.commit()
                # 텔레그램도 너무 빠르게 보내면 안 됨
                await asyncio.sleep(1.5)
        except Exception as e:
            log.exception("telegram report failed")
            _audit(db, "telegram.report_error", target=f"batch:{batch_id}",
                   detail={"error": str(e)[:200]}, actor="telegram")
            db.commit()

        _set_agent(db, "telegram", "대기")
        batch.status = "done"
        _audit(db, "batch.finished", target=f"batch:{batch_id}",
               detail={"completed": completed, "failed": failed})
        db.commit()

    except Exception as e:
        log.exception(f"run_batch {batch_id} crashed")
        try:
            batch = db.get(Batch, batch_id)
            if batch:
                batch.status = "failed"
                batch.error = str(e)[:500]
                _audit(db, "batch.crashed", target=f"batch:{batch_id}",
                       detail={"error": str(e)[:200]})
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def _process_one(db: Session, batch: Batch, issue: str, idx: int) -> None:
    """단일 이슈 → 작곡가 → Mureka → 폴링까지 처리."""
    # 1) 작곡가 기획
    _set_agent(db, "songwriter", f"#{idx}/{len(batch.issues or [])} 기획 중")
    db.commit()

    plan = await songwriter_compose(issue, db=db)

    # 2) Job 생성 (배치 연결)
    job = Job(
        kind="music_generate",
        department_slug="music",
        agent_slug="music_producer",
        status="pending",
        batch_id=batch.id,
        input={
            "issue": issue,
            "title": plan["title"],
            "style": plan["style"],
            "lyrics_len": len(plan["lyrics"]),
            "mood": plan.get("mood"),
            "source": plan.get("source"),
        },
    )
    db.add(job)
    db.flush()
    _audit(db, "music.song_planned", target=f"job:{job.id}",
           detail={"title": plan["title"], "mood": plan.get("mood"), "source": plan.get("source")},
           actor="songwriter")
    db.commit()

    # 3) Mureka 호출
    _set_agent(db, "music_producer", f"#{idx} Mureka 호출 중")
    db.commit()

    payload = {"lyrics": plan["lyrics"], "style": plan["style"], "title": plan["title"]}
    result = await call_api(db, provider="mureka", operation="generate",
                            payload=payload, requester="music_producer")

    job = db.get(Job, job.id)
    if not result.get("ok"):
        job.status = "failed"
        job.error = result.get("error", "Mureka generate 실패")[:500]
        _audit(db, "music.generate_failed", target=f"job:{job.id}",
               detail={"error": job.error[:120]})
        db.commit()
        return

    data = result.get("data") or {}
    ext_id = data.get("id") or data.get("task_id") or data.get("song_id")
    if not ext_id:
        job.status = "failed"
        job.error = f"Mureka 응답에 id 없음: {str(data)[:200]}"
        _audit(db, "music.generate_failed", target=f"job:{job.id}", detail={"reason": "no_id"})
        db.commit()
        return

    job.external_id = str(ext_id)
    job.status = "running"
    job.output = {"submitted": data}
    _audit(db, "music.generate_submitted", target=f"job:{job.id}",
           detail={"external_id": ext_id})
    db.commit()

    # 4) 폴링
    _set_agent(db, "music_producer", f"#{idx} 결과 대기")
    db.commit()

    deadline = datetime.utcnow() + timedelta(seconds=POLL_TIMEOUT_SEC)
    while datetime.utcnow() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        q = await call_api(db, provider="mureka", operation="query",
                           payload={"id": ext_id}, requester="music_producer")
        if not q.get("ok"):
            # 일시적 에러일 수 있으니 deadline까지 계속 시도
            continue

        qdata = q.get("data") or {}
        mstatus = (qdata.get("status") or "").lower()

        # 다양한 응답 형태에서 audio_url 후보 추출
        audio_url = (
            qdata.get("audio_url")
            or qdata.get("url")
            or (qdata.get("song") or {}).get("audio_url")
            or (qdata.get("data") or {}).get("audio_url")
        )
        # 응답에 choices/songs 같은 배열이 있을 경우 첫 항목
        if not audio_url:
            for k in ("choices", "songs", "results"):
                arr = qdata.get(k)
                if isinstance(arr, list) and arr:
                    item = arr[0]
                    if isinstance(item, dict):
                        audio_url = item.get("audio_url") or item.get("url") or item.get("audio")
                        if audio_url:
                            break

        job = db.get(Job, job.id)
        job.output = qdata

        if mstatus in {"succeeded", "success", "done", "completed", "finished"} or audio_url:
            if audio_url:
                # 출력에 audio_url을 한 번 더 평탄화해서 저장
                job.output = {**qdata, "audio_url": audio_url}
            job.status = "done"
            _audit(db, "music.generate_done", target=f"job:{job.id}",
                   detail={"has_audio": bool(audio_url)})
            db.commit()

            # 자동 다운로드 보관 (사이트 재생용)
            if audio_url:
                try:
                    db.refresh(job)
                    await archiver.download_and_archive(db, job)
                except Exception as e:
                    log.exception(f"archive failed for job {job.id}")
                    _audit(db, "audio.archive_failed", target=f"job:{job.id}",
                           detail={"error": str(e)[:200]}, actor="archiver")
                    db.commit()
            return

        if mstatus in {"failed", "error", "rejected"}:
            job.status = "failed"
            job.error = str(qdata.get("error") or qdata.get("message") or "mureka 실패")[:500]
            _audit(db, "music.generate_failed", target=f"job:{job.id}",
                   detail={"error": job.error[:120]})
            db.commit()
            return

        # 진행 중이면 계속
        db.commit()

    # 타임아웃
    job = db.get(Job, job.id)
    job.status = "failed"
    job.error = f"폴링 타임아웃 ({POLL_TIMEOUT_SEC}s)"
    _audit(db, "music.generate_timeout", target=f"job:{job.id}")
    db.commit()

async def make_video_for_archived_job(job_id: int) -> None:
    """보관된 곡 1개를 영상으로 만들고 텔레그램으로 보낸다.
    배치와 무관하게 사이트에서 사용자가 체크한 곡 처리용. 자체 DB 세션 사용."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            log.warning(f"make_video_for_archived_job: job {job_id} not found")
            return
        if job.status != "done":
            log.warning(f"make_video_for_archived_job: job {job_id} status={job.status}, skip")
            return
        # batch 없이 영상 만들기 (Video.batch_id는 nullable)
        await _make_and_send_video(db, batch=None, job=job)
        db.commit()
    except Exception as e:
        log.exception(f"make_video_for_archived_job {job_id} crashed")
        try:
            _audit(db, "video.adhoc_crash", target=f"job:{job_id}",
                   detail={"error": str(e)[:200]}, actor="video_editor")
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


async def _make_and_send_video(db: Session, batch: Batch | None, job: Job) -> None:
    """곡 1개에 대한 mp4 만들고 텔레그램으로 전송.

    실패 시 audio만이라도 보내도록 폴백.
    """
    audio_url = (job.output or {}).get("audio_url")
    title = (job.input or {}).get("title", f"곡 #{job.id}")
    issue = (job.input or {}).get("issue", "")
    style = (job.input or {}).get("style", "")
    mood = (job.input or {}).get("mood", "modern")

    if not audio_url:
        # 오디오 URL 없으면 영상 못 만듦 — audio 폴백
        await telegram_agent.send_audio_for_job(db, job)
        return

    # Video 레코드 생성 (batch 없으면 batch_id=None)
    video = Video(
        job_id=job.id,
        batch_id=batch.id if batch else None,
        status="rendering",
    )
    db.add(video)
    db.flush()

    _set_agent(db, "video_editor", f"#{job.id} 영상 인코딩")
    _audit(db, "video.render_started", target=f"video:{video.id}",
           detail={"job_id": job.id, "title": title}, actor="video_editor")
    db.commit()

    # 영상 만들기
    result = await video_maker.make_video_for_job(
        job_id=job.id, audio_url=audio_url,
        title=title, mood=mood, subtitle=issue,
    )

    video = db.get(Video, video.id)
    if not result.get("ok"):
        video.status = "failed"
        video.error = result.get("error", "")[:500]
        _audit(db, "video.render_failed", target=f"video:{video.id}",
               detail={"error": video.error[:120]}, actor="video_editor")
        db.commit()
        # 영상 실패 → audio라도 보내기
        await telegram_agent.send_audio_for_job(db, job)
        return

    video.status = "done"
    video.image_path = result.get("image_path", "")
    video.video_path = result.get("video_path", "")
    video.duration_sec = result.get("duration_sec", 0)
    video.file_size = result.get("file_size", 0)
    _audit(db, "video.render_done", target=f"video:{video.id}",
           detail={"duration": video.duration_sec, "size_mb": video.file_size // 1024 // 1024},
           actor="video_editor")
    db.commit()

    # 텔레그램으로 mp4 전송
    _set_agent(db, "telegram", f"#{job.id} 영상 전송 중")
    db.commit()

    caption = (
        f"<b>#{job.id} · {title}</b>\n"
        f"{('<i>' + issue + '</i>' + chr(10)) if issue else ''}"
        f"🎼 {style}\n"
        f"⏱ {video.duration_sec}s · 💾 {video.file_size // 1024 // 1024}MB\n\n"
        f"채택은 이 메시지에 <b>✓</b> · 거절은 <b>✗</b> 로 답장"
    )
    send_result = await telegram_agent.send_video_file(
        db, video_path=video.video_path, caption=caption, job_id=job.id,
    )

    if send_result.get("ok"):
        msg_id = ((send_result.get("data") or {}).get("result") or {}).get("message_id")
        if msg_id:
            video.telegram_sent = True
            video.telegram_message_id = int(msg_id)
            job.telegram_message_id = int(msg_id)
            job.review_status = "pending_review"
            _audit(db, "telegram.video_sent", target=f"video:{video.id}",
                   detail={"message_id": msg_id}, actor="telegram")
        db.commit()
    else:
        _audit(db, "telegram.video_send_failed", target=f"video:{video.id}",
               detail={"error": send_result.get("error", "")[:200]}, actor="telegram")
        db.commit()
        # 영상 못 보냈으면 audio 폴백
        await telegram_agent.send_audio_for_job(db, job)
        db.commit()
