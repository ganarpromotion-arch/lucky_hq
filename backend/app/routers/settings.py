"""
본부 설정 API
- GET  /api/settings           : 모든 설정 (secret은 마스킹)
- GET  /api/settings/{key}     : 단건 (마스킹)
- PUT  /api/settings/{key}     : 등록/갱신  body: {"value": "...", "is_secret": true|false}
- DELETE /api/settings/{key}   : 삭제

Mureka 등 외부 API 키는 여기서 관리. is_secret=True면 응답에서 마스킹.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Setting, AuditLog

router = APIRouter(prefix="/api/settings", tags=["settings"])


# 본부가 사용하는 외부 API 카탈로그 (UI 통합 관리 화면용)
# - 신규 provider 추가 시 여기에 한 줄 추가하면 /secrets 페이지에 자동 노출
SECRETS_CATALOG: list[dict] = [
    {
        "key": "mureka_api_key",
        "label": "Mureka",
        "description": "음악 생성 API. 음악제작 부서가 곡을 만들 때 사용.",
        "docs_url": "https://platform.mureka.ai",
        "used_by": ["music_producer"],
        "required": True,
    },
    {
        "key": "anthropic_api_key",
        "label": "Anthropic Claude",
        "description": "작곡가 직원이 가사를 작성하는 LLM. Railway env에 있으면 자동 사용.",
        "docs_url": "https://console.anthropic.com",
        "used_by": ["songwriter"],
        "required": False,
    },
    {
        "key": "openai_api_key",
        "label": "OpenAI",
        "description": "작곡가 LLM 폴백. Anthropic 실패 시 자동 시도.",
        "docs_url": "https://platform.openai.com",
        "used_by": ["songwriter", "curator"],
        "required": False,
    },
    # ── 일일 배치 옵션 (시크릿 아님, 평문 보임) ──────────────────────────
    {
        "key": "curator_seed_text",
        "label": "큐레이터 시드 텍스트",
        "description": "비워두면 LLM이 알아서 큐레이션. 채우면 그 날 곡들에 반영.",
        "docs_url": "",
        "used_by": ["curator"],
        "required": False,
        "is_secret": False,
    },
    {
        "key": "batch_schedule_hour",
        "label": "일일 배치 시각 (시)",
        "description": "0~23. 기본 9 (KST 09:00).",
        "docs_url": "",
        "used_by": ["scheduler"],
        "required": False,
        "is_secret": False,
    },
    {
        "key": "daily_song_count",
        "label": "하루 곡 수",
        "description": "기본 10. 1~20 권장.",
        "docs_url": "",
        "used_by": ["batch_runner"],
        "required": False,
        "is_secret": False,
    },
    {
        "key": "batch_review_minutes",
        "label": "검토창 시간 (분)",
        "description": "10곡 ❌ 검토 마감까지의 분 수. 기본 60.",
        "docs_url": "",
        "used_by": ["batch_runner", "scheduler"],
        "required": False,
        "is_secret": False,
    },
]


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * 8 + value[-4:]


def _serialize(s: Setting) -> dict:
    return {
        "key": s.key,
        "value": _mask(s.value) if s.is_secret else s.value,
        "is_secret": s.is_secret,
        "has_value": bool(s.value),
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


class SettingUpsert(BaseModel):
    value: str
    is_secret: bool = True


@router.get("")
def list_settings(db: Session = Depends(get_db)):
    rows = db.query(Setting).order_by(Setting.key).all()
    return [_serialize(r) for r in rows]


@router.get("/catalog")
def get_catalog(db: Session = Depends(get_db)):
    """모든 등록 가능한 API 키 + 현재 등록 상태."""
    by_key = {r.key: r for r in db.query(Setting).all()}
    out = []
    for entry in SECRETS_CATALOG:
        row = by_key.get(entry["key"])
        out.append({
            **entry,
            "value": _mask(row.value) if row and row.is_secret else (row.value if row else ""),
            "has_value": bool(row and row.value),
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })
    return out


@router.get("/{key}")
def get_setting(key: str, db: Session = Depends(get_db)):
    row = db.query(Setting).filter_by(key=key).first()
    if not row:
        return {"key": key, "value": "", "is_secret": True, "has_value": False, "updated_at": None}
    return _serialize(row)


@router.put("/{key}")
def upsert_setting(key: str, body: SettingUpsert, db: Session = Depends(get_db)):
    if not key or len(key) > 64:
        raise HTTPException(400, "invalid key")
    row = db.query(Setting).filter_by(key=key).first()
    is_new = row is None
    if not row:
        row = Setting(key=key, value=body.value, is_secret=body.is_secret)
        db.add(row)
    else:
        row.value = body.value
        row.is_secret = body.is_secret
    db.add(AuditLog(
        actor="owner",
        action="settings.upsert" if not is_new else "settings.create",
        target=f"key:{key}",
        detail={"is_secret": body.is_secret, "has_value": bool(body.value)},
    ))
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.delete("/{key}")
def delete_setting(key: str, db: Session = Depends(get_db)):
    row = db.query(Setting).filter_by(key=key).first()
    if row:
        db.delete(row)
        db.add(AuditLog(actor="owner", action="settings.delete", target=f"key:{key}"))
        db.commit()
    return {"ok": True}
