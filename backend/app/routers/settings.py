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
