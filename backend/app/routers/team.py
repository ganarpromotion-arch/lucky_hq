"""
팀 (멀티 사용자) 라우터

- POST /api/team/codes              : owner가 1회용 가입 코드 발급
- GET  /api/team/codes              : 미사용 코드 목록
- DELETE /api/team/codes/{code}     : 코드 폐기

- GET  /api/team/members            : 멤버 목록
- PATCH /api/team/members/{chat_id} : 역할 변경 / 알림 설정
- DELETE /api/team/members/{chat_id}: 멤버 제거 (강퇴)

- POST /api/team/join (텔레그램 봇 내부 전용)
"""
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc

from ..db import get_db
from ..models import TelegramSubscriber, TelegramJoinCode, AuditLog
from ..config import get_settings

router = APIRouter(prefix="/api/team", tags=["team"])


# 역할별 권한 정의
ROLES = ["owner", "manager", "operator", "approver", "viewer", "guest"]
ROLE_LABELS = {
    "owner":    "오너",
    "manager":  "최고 팀장",
    "operator": "운영자",
    "approver": "승인자",
    "viewer":   "뷰어",
    "guest":    "게스트",
}


def _gen_code(n: int = 6) -> str:
    """대문자+숫자 6자리 코드."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 문자 제외
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ── 가입 코드 ───────────────────────────────────────
class CreateCodeRequest(BaseModel):
    role: str = Field(default="manager")
    expires_hours: int = Field(default=24, ge=1, le=720)


@router.post("/codes")
def create_code(req: CreateCodeRequest, db: Session = Depends(get_db)):
    if req.role not in ROLES:
        raise HTTPException(400, f"역할은 {ROLES} 중 하나")
    code = _gen_code()
    # 중복 회피 (낮은 확률이지만)
    while db.query(TelegramJoinCode).filter_by(code=code).first():
        code = _gen_code()

    row = TelegramJoinCode(
        code=code,
        role=req.role,
        expires_at=datetime.utcnow() + timedelta(hours=req.expires_hours),
    )
    db.add(row)
    db.add(AuditLog(actor="owner", action="team.code_created",
                    detail={"code": code, "role": req.role}))
    db.commit()
    db.refresh(row)
    return {
        "code": row.code,
        "role": row.role,
        "role_label": ROLE_LABELS.get(row.role, row.role),
        "expires_at": row.expires_at.isoformat(),
        "instructions": f"신규 멤버에게 다음을 전달하세요:\n1. 텔레그램에서 봇과 대화 시작\n2. /join {row.code} 보내기",
    }


@router.get("/codes")
def list_codes(db: Session = Depends(get_db)):
    rows = db.query(TelegramJoinCode).filter_by(used=False)\
        .order_by(desc(TelegramJoinCode.created_at)).limit(50).all()
    out = []
    now = datetime.utcnow()
    for r in rows:
        expired = r.expires_at and r.expires_at < now
        if expired:
            continue
        out.append({
            "code": r.code,
            "role": r.role,
            "role_label": ROLE_LABELS.get(r.role, r.role),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "created_at": r.created_at.isoformat(),
        })
    return out


@router.delete("/codes/{code}")
def revoke_code(code: str, db: Session = Depends(get_db)):
    row = db.query(TelegramJoinCode).filter_by(code=code).first()
    if not row:
        raise HTTPException(404, "코드 없음")
    db.delete(row)
    db.add(AuditLog(actor="owner", action="team.code_revoked", detail={"code": code}))
    db.commit()
    return {"ok": True}


# ── 멤버 관리 ───────────────────────────────────────
class UpdateMemberRequest(BaseModel):
    role: str | None = None
    nickname: str | None = None
    is_active: bool | None = None
    receives_song_reports: bool | None = None
    receives_chat_replies: bool | None = None


@router.get("/members")
def list_members(db: Session = Depends(get_db)):
    settings = get_settings()
    owner_chat_id = settings.telegram_owner_chat_id

    rows = db.query(TelegramSubscriber).order_by(TelegramSubscriber.id).all()
    out = []
    # owner는 별도 표시 (subscriber에 등록 안 돼 있어도)
    if owner_chat_id and not any(r.chat_id == str(owner_chat_id) for r in rows):
        out.append({
            "chat_id": str(owner_chat_id),
            "role": "owner",
            "role_label": ROLE_LABELS["owner"],
            "nickname": "(env에 등록된 owner)",
            "username": "",
            "is_active": True,
            "receives_song_reports": True,
            "receives_chat_replies": True,
            "joined_at": None,
            "is_env_owner": True,
        })
    for r in rows:
        out.append({
            "chat_id": r.chat_id,
            "role": r.role,
            "role_label": ROLE_LABELS.get(r.role, r.role),
            "nickname": r.nickname,
            "username": r.username,
            "first_name": r.first_name,
            "is_active": r.is_active,
            "receives_song_reports": r.receives_song_reports,
            "receives_chat_replies": r.receives_chat_replies,
            "joined_at": r.joined_at.isoformat() if r.joined_at else None,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "is_env_owner": False,
        })
    return out


@router.patch("/members/{chat_id}")
def update_member(chat_id: str, req: UpdateMemberRequest, db: Session = Depends(get_db)):
    row = db.query(TelegramSubscriber).filter_by(chat_id=chat_id).first()
    if not row:
        raise HTTPException(404, "멤버 없음")
    changes = {}
    if req.role is not None:
        if req.role not in ROLES:
            raise HTTPException(400, f"역할은 {ROLES} 중 하나")
        if req.role == "owner":
            raise HTTPException(400, "owner는 env로만 지정 가능")
        row.role = req.role; changes["role"] = req.role
    if req.nickname is not None:
        row.nickname = req.nickname; changes["nickname"] = req.nickname
    if req.is_active is not None:
        row.is_active = req.is_active; changes["is_active"] = req.is_active
    if req.receives_song_reports is not None:
        row.receives_song_reports = req.receives_song_reports
        changes["receives_song_reports"] = req.receives_song_reports
    if req.receives_chat_replies is not None:
        row.receives_chat_replies = req.receives_chat_replies
        changes["receives_chat_replies"] = req.receives_chat_replies
    db.add(AuditLog(actor="owner", action="team.member_updated",
                    target=f"chat:{chat_id}", detail=changes))
    db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/members/{chat_id}")
def remove_member(chat_id: str, db: Session = Depends(get_db)):
    row = db.query(TelegramSubscriber).filter_by(chat_id=chat_id).first()
    if not row:
        raise HTTPException(404, "멤버 없음")
    db.delete(row)
    db.add(AuditLog(actor="owner", action="team.member_removed", target=f"chat:{chat_id}"))
    db.commit()
    return {"ok": True}
