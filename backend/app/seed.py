"""
초기 시드 데이터.

- 부서: 음악제작
- 직원: 음악 프로듀서, 작곡가 (둘 다 음악부서) → 활성
- 다른 직원들은 시드 데이터로 만들어두지만 is_active=False (UI에서 숨김)
- v1.x 진행하며 직원 한 명씩 활성화

이미 있으면 활성/비활성 상태와 이름·아바타·voice는 갱신.
"""
from .db import SessionLocal
from .models import Department, Agent


DEPARTMENTS = [
    {"slug": "music", "name": "음악제작", "status": "piloting",
     "description": "Mureka API로 가사+스타일을 곡으로 만드는 부서."},
]


# is_active=True 인 직원만 메인 콘솔/부서 화면에 노출
AGENTS = [
    # ── 음악부서 (활성) ───────────────────────────────
    {"slug": "music_producer", "name": "음악 프로듀서",
     "role": "music_producer", "avatar": "🎵",
     "voice": "음악제작 부서 대표. Mureka로 곡을 만들고 결과를 검수한다.",
     "department": "music", "is_active": True},
    {"slug": "songwriter", "name": "작곡가",
     "role": "songwriter", "avatar": "🎼",
     "voice": "최근 이슈를 받아 제목·가사·스타일을 기획한다.",
     "department": "music", "is_active": True},

    # ── 비활성 (시드만, UI 숨김) ─────────────────────
    {"slug": "commander", "name": "지휘관", "role": "commander", "avatar": "🎯",
     "voice": "자연어 명령의 단일 진입점.", "is_active": False},
    {"slug": "control", "name": "관제", "role": "control", "avatar": "📡",
     "voice": "전체 직원 상태/큐/락을 감시한다.", "is_active": False},
    {"slug": "telegram", "name": "텔레그램", "role": "telegram", "avatar": "✈️",
     "voice": "owner 텔레그램 채널 입출력 담당.", "is_active": False},
    {"slug": "api_manager", "name": "API 관리", "role": "api_manager", "avatar": "🗝️",
     "voice": "외부 API 키/세션을 단독 관리.", "is_active": False},
    {"slug": "blog_validator", "name": "블로그 검증", "role": "validator", "avatar": "📰",
     "voice": "외부 블로그 콘텐츠 검증·테스트·보고만.", "is_active": False},
    {"slug": "shorts_validator", "name": "쇼츠 검증", "role": "validator", "avatar": "🎬",
     "voice": "외부 쇼츠 채널 검증·테스트·보고만.", "is_active": False},
    {"slug": "order_delivery", "name": "주문/배송", "role": "operations", "avatar": "🛵",
     "voice": "진솔정 주문배송 도메인 운영.", "is_active": False},
    {"slug": "marketing_qa", "name": "마케팅 검수", "role": "reviewer", "avatar": "📣",
     "voice": "마케팅 산출물 검수.", "is_active": False},
    {"slug": "writing_qa", "name": "글쓰기 검수", "role": "reviewer", "avatar": "✍️",
     "voice": "글쓰기 산출물 검수.", "is_active": False},
    {"slug": "dev_test", "name": "개발/테스트", "role": "engineer", "avatar": "🛠️",
     "voice": "본부 코드/테스트 담당.", "is_active": False},
    {"slug": "learning", "name": "학습", "role": "learning", "avatar": "📚",
     "voice": "본부 누적 데이터로 학습.", "is_active": False},
    {"slug": "approver", "name": "승인", "role": "approver", "avatar": "✅",
     "voice": "위험 작업 owner 승인 라우팅.", "is_active": False},
]


def run_seed() -> None:
    db = SessionLocal()
    try:
        dept_map: dict[str, int] = {}
        for d in DEPARTMENTS:
            row = db.query(Department).filter_by(slug=d["slug"]).first()
            if not row:
                row = Department(**d)
                db.add(row); db.flush()
            else:
                row.name = d["name"]; row.status = d["status"]; row.description = d["description"]
            dept_map[d["slug"]] = row.id

        for a in AGENTS:
            data = dict(a)
            dept_slug = data.pop("department", None)
            row = db.query(Agent).filter_by(slug=data["slug"]).first()
            if row:
                for k in ("name", "role", "avatar", "voice", "is_active"):
                    if k in data:
                        setattr(row, k, data[k])
            else:
                row = Agent(**data); db.add(row); db.flush()
            row.department_id = dept_map.get(dept_slug) if dept_slug else None
        db.commit()
    finally:
        db.close()
