"""
일일 배치 오케스트레이터 (Phase 1)

흐름:
  1) Mureka 잔량 자동 체크 — 임계 미만이면 skip + AuditLog 알림
  2) 큐레이터 직원: 오늘의 (issue, concept) N개 산출
  3) 작사가 직원: 각 issue → (title, lyrics, style) — N회 병렬 호출
  4) Mureka 순차 호출 (동시 1곡 제한): generate → 폴링 → done|failed
  5) 검토 마감 deadline 설정 → status=awaiting_review

Phase 2: Telegram 알림. Phase 3: ffmpeg + YouTube.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .api_manager import call_api
from .config import get_settings
from .curator import curate
from .db import SessionLocal
from .models import AuditLog, Batch, Job, Setting
from .songwriter import compose_plan as songwriter_compose


def _today_kst_str() -> str:
    tz_name = get_settings().batch_timezone or "Asia/Seoul"
    try:
        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


# ─── 잔량 체크 ─────────────────────────────────────────
def _resolve_balance(data: Any) -> float | None:
    """Mureka /v1/account/billing 응답에서 잔액 숫자 추출 (응답 키가 확정 전이라 후보 다수 처리)."""
    if not isinstance(data, dict):
        return None
    for key in ("balance", "credits", "remaining", "remain", "available"):
        v = data.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.replace(",", ""))
            except Exception:
                pass
    # 중첩: data.balance.amount 같은 경우
    for key in ("balance", "data", "account"):
        nested = data.get(key)
        if isinstance(nested, dict):
            r = _resolve_balance(nested)
            if r is not None:
                return r
    return None


async def check_mureka_balance(db: Session) -> dict:
    """잔량 조회. {ok, balance, raw, error}."""
    result = await call_api(
        db, provider="mureka", operation="billing",
        payload={}, requester="batch_runner",
    )
    raw = result.get("data") if result.get("ok") else None
    return {
        "ok": result.get("ok", False),
        "status_code": result.get("status_code", 0),
        "balance": _resolve_balance(raw),
        "raw": raw,
        "error": result.get("error", ""),
    }


# ─── Mureka 한 곡 처리 (generate + polling) ─────────────
async def _generate_one_song(
    db: Session, job: Job, lyrics: str, prompt: str, timeout_seconds: int
) -> None:
    """Mureka에 generate 요청 → 폴링 → job 상태 업데이트.
    예외/타임아웃은 job.status='failed'로 남기고 swallow."""
    try:
        result = await call_api(
            db, provider="mureka", operation="generate",
            payload={"lyrics": lyrics, "prompt": prompt, "model": "auto"},
            requester="batch_runner",
        )
        # 다시 fetch (다른 트랜잭션에서 변경됐을 수 있음)
        job = db.get(Job, job.id)
        if not result.get("ok"):
            job.status = "failed"
            job.error = result.get("error", "")
            db.commit()
            return

        data = result.get("data") or {}
        ext_id = data.get("id") or data.get("task_id") or data.get("song_id")
        if not ext_id:
            job.status = "failed"
            job.error = "Mureka 응답에 id 없음"
            db.commit()
            return
        job.external_id = str(ext_id)
        job.status = "running"
        job.output = {"submitted": data}
        db.commit()

        # 폴링
        deadline = datetime.utcnow() + timedelta(seconds=timeout_seconds)
        poll_interval = 5.0
        while datetime.utcnow() < deadline:
            await asyncio.sleep(poll_interval)
            qr = await call_api(
                db, provider="mureka", operation="query",
                payload={"id": str(ext_id)}, requester="batch_runner",
            )
            if not qr.get("ok"):
                # 일시 오류면 다음 폴 까지 그냥 진행
                continue
            qd = qr.get("data") or {}
            mstatus = (qd.get("status") or "").lower()
            job = db.get(Job, job.id)
            job.output = qd
            if mstatus == "succeeded":
                job.status = "done"
                db.commit()
                return
            if mstatus in {"failed", "error", "timeouted", "cancelled"}:
                job.status = "failed"
                job.error = str(qd.get("failed_reason") or qd.get("error") or qd.get("message") or f"mureka {mstatus}")
                db.commit()
                return
            db.commit()
        # 시간 초과
        job = db.get(Job, job.id)
        if job.status not in {"done", "failed"}:
            job.status = "failed"
            job.error = f"폴링 타임아웃 ({timeout_seconds}s)"
            db.commit()
    except Exception as e:
        try:
            job = db.get(Job, job.id)
            if job and job.status not in {"done"}:
                job.status = "failed"
                job.error = f"예외: {type(e).__name__}: {e}"[:500]
                db.commit()
        except Exception:
            db.rollback()


# ─── 메인 진입점 ─────────────────────────────────────────
def _seed_text_from_settings(db: Session) -> str:
    row = db.query(Setting).filter_by(key="curator_seed_text").first()
    return row.value if row and row.value else ""


async def run_daily_batch(triggered_by: str = "scheduler") -> dict:
    """오늘의 배치를 끝까지 실행한다. 새 DB 세션을 만들어 self-managed.
    Returns: {"batch_id": int, "created": int, "done": int, "failed": int, "skipped_reason": str|None}.
    """
    db: Session = SessionLocal()
    settings = get_settings()
    today = _today_kst_str()
    count = max(1, int(settings.daily_song_count or 10))

    try:
        # 1) Batch 행 생성 (오늘 이미 있으면 재사용)
        batch = (
            db.query(Batch)
            .filter(Batch.run_date == today, Batch.department_slug == "music")
            .order_by(Batch.id.desc())
            .first()
        )
        if batch and batch.status not in {"failed"}:
            return {
                "batch_id": batch.id,
                "skipped_reason": f"오늘({today}) 이미 배치가 존재 (status={batch.status})",
                "created": 0, "done": 0, "failed": 0,
            }

        batch = Batch(
            department_slug="music",
            run_date=today,
            status="generating",
            target_count=count,
        )
        db.add(batch)
        db.flush()
        batch_id = batch.id
        db.add(AuditLog(actor="batch_runner", action="batch.start",
                        target=f"batch:{batch_id}",
                        detail={"triggered_by": triggered_by, "count": count}))
        db.commit()

        # 2) 잔량 체크
        bal = await check_mureka_balance(db)
        if not bal["ok"]:
            batch = db.get(Batch, batch_id)
            batch.status = "failed"
            batch.error = f"잔량 조회 실패: {bal.get('error', '')}"
            db.add(AuditLog(actor="batch_runner", action="batch.skipped",
                            target=f"batch:{batch_id}",
                            detail={"reason": "billing_unreachable", "error": batch.error}))
            db.commit()
            return {"batch_id": batch_id, "skipped_reason": batch.error,
                    "created": 0, "done": 0, "failed": 0}

        threshold = float(settings.mureka_min_balance_threshold or 0)
        if bal["balance"] is not None and bal["balance"] < threshold:
            batch = db.get(Batch, batch_id)
            batch.status = "failed"
            batch.error = f"Mureka 잔량 부족 ({bal['balance']} < {threshold})"
            db.add(AuditLog(actor="batch_runner", action="batch.skipped",
                            target=f"batch:{batch_id}",
                            detail={"reason": "low_balance", "balance": bal["balance"]}))
            db.commit()
            return {"batch_id": batch_id, "skipped_reason": batch.error,
                    "created": 0, "done": 0, "failed": 0}

        # 3) 큐레이터
        seed_text = _seed_text_from_settings(db)
        curated = await curate(db, count=count, seed_text=seed_text)
        themes = curated.get("themes") or []
        batch = db.get(Batch, batch_id)
        batch.curated_themes = curated
        db.add(AuditLog(actor="curator", action="curator.curated",
                        target=f"batch:{batch_id}",
                        detail={"count": len(themes), "source": curated.get("source")}))
        db.commit()

        if not themes:
            batch = db.get(Batch, batch_id)
            batch.status = "failed"
            batch.error = "큐레이터가 테마 0개 산출"
            db.commit()
            return {"batch_id": batch_id, "skipped_reason": batch.error,
                    "created": 0, "done": 0, "failed": 0}

        # 4) 작사가 — 병렬 호출 (LLM 호출은 외부 API라 동시 OK)
        async def _compose(t: dict) -> dict:
            issue = t.get("issue", "")
            try:
                plan = await songwriter_compose(issue, db=db)
            except Exception as e:
                plan = {"title": issue[:20] or "제목 없음",
                        "lyrics": "", "style": t.get("concept", "K-pop, 100 BPM"),
                        "mood": "modern", "keyword": "", "source": f"error:{e}"}
            # 큐레이터 컨셉이 있으면 style을 보강 (없으면 작사가가 만든 style 유지)
            concept = (t.get("concept") or "").strip()
            if concept and not plan.get("style"):
                plan["style"] = concept
            plan["_issue"] = issue
            plan["_concept"] = concept
            return plan

        plans = await asyncio.gather(*[_compose(t) for t in themes])
        db.add(AuditLog(actor="songwriter", action="songwriter.batch_compose",
                        target=f"batch:{batch_id}",
                        detail={"count": len(plans)}))
        db.commit()

        # 5) Mureka 순차 (동시 1곡 제한) — 각 곡마다 Job 생성 후 generate+polling
        timeout = int(settings.batch_song_timeout_seconds or 600)
        created = 0
        for i, plan in enumerate(plans):
            lyrics = (plan.get("lyrics") or "").strip()
            if not lyrics:
                # 가사 비어있으면 skip (failed Job 한 줄 남김)
                job = Job(
                    kind="music_generate",
                    department_slug="music",
                    agent_slug="music_producer",
                    status="failed",
                    input={"title": plan.get("title", ""),
                           "style": plan.get("style", ""),
                           "issue": plan.get("_issue", ""),
                           "concept": plan.get("_concept", ""),
                           "index": i},
                    error="가사 비어있음 (작사가 실패)",
                    batch_id=batch_id,
                )
                db.add(job)
                db.commit()
                continue

            job = Job(
                kind="music_generate",
                department_slug="music",
                agent_slug="music_producer",
                status="pending",
                input={"title": plan.get("title", ""),
                       "style": plan.get("style", ""),
                       "lyrics_len": len(lyrics),
                       "issue": plan.get("_issue", ""),
                       "concept": plan.get("_concept", ""),
                       "index": i,
                       "mood": plan.get("mood", "")},
                batch_id=batch_id,
            )
            db.add(job)
            db.flush()
            db.commit()
            created += 1

            await _generate_one_song(
                db, job,
                lyrics=lyrics,
                prompt=plan.get("style") or plan.get("_concept") or "K-pop, 100 BPM",
                timeout_seconds=timeout,
            )

        # 6) Batch 마감일 + 상태 전환
        batch = db.get(Batch, batch_id)
        review_min = int(settings.batch_review_minutes or 60)
        batch.deadline_at = datetime.utcnow() + timedelta(minutes=review_min)
        batch.status = "awaiting_review"
        db.add(AuditLog(actor="batch_runner", action="batch.awaiting_review",
                        target=f"batch:{batch_id}",
                        detail={"deadline_at": batch.deadline_at.isoformat(),
                                "review_minutes": review_min}))
        db.commit()

        # 통계
        jobs = db.query(Job).filter(Job.batch_id == batch_id).all()
        done = sum(1 for j in jobs if j.status == "done")
        failed = sum(1 for j in jobs if j.status == "failed")

        return {
            "batch_id": batch_id,
            "created": created,
            "done": done,
            "failed": failed,
            "skipped_reason": None,
        }
    finally:
        db.close()
