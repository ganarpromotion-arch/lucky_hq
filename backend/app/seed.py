"""
초기 시드 데이터: 부서 1개(음악제작) + 직원 12명.
앱 시작 시 자동 실행. 이미 있으면 건너뜀.
"""
from .db import SessionLocal
from .models import Department, Agent


# 메모리에 정리된 v1 직원 12명
AGENTS = [
    {"slug": "commander",       "name": "지휘관",       "role": "commander",       "avatar": "🎯", "voice": "자연어 명령의 단일 진입점. 의도 분류 → 안전 게이트 → 분배."},
    {"slug": "control",         "name": "관제",         "role": "control",         "avatar": "📡", "voice": "전체 직원 상태/큐/락을 감시한다."},
    {"slug": "telegram",        "name": "텔레그램",     "role": "telegram",        "avatar": "✈️", "voice": "owner 텔레그램 채널 입출력 담당."},
    {"slug": "api_manager",     "name": "API 관리",     "role": "api_manager",     "avatar": "🗝️", "voice": "외부 API 키/세션을 단독 관리. call_api 단일 인터페이스."},
    {"slug": "blog_validator",  "name": "블로그 검증",  "role": "validator",       "avatar": "📰", "voice": "외부 블로그 콘텐츠 수정 없이 검증·테스트·보고만."},
    {"slug": "shorts_validator","name": "쇼츠 검증",    "role": "validator",       "avatar": "🎬", "voice": "외부 쇼츠 채널 검증·테스트·보고만."},
    {"slug": "order_delivery",  "name": "주문/배송",    "role": "operations",      "avatar": "🛵", "voice": "진솔정 주문배송 도메인 운영."},
    {"slug": "marketing_qa",    "name": "마케팅 검수",  "role": "reviewer",        "avatar": "📣", "voice": "마케팅 산출물 검수."},
    {"slug": "writing_qa",      "name": "글쓰기 검수",  "role": "reviewer",        "avatar": "✍️", "voice": "글쓰기 산출물 검수."},
    {"slug": "dev_test",        "name": "개발/테스트",  "role": "engineer",        "avatar": "🛠️", "voice": "본부 코드/테스트 담당."},
    {"slug": "learning",        "name": "학습",         "role": "learning",        "avatar": "📚", "voice": "본부 누적 데이터로 직원/디렉티브 학습."},
    {"slug": "approver",        "name": "승인",         "role": "approver",        "avatar": "✅", "voice": "위험 작업 owner 승인 라우팅."},
    # 음악제작은 부서이지만 콘솔 좌측에선 한 명의 '대표 직원'으로 노출 (메모리 정책)
    {"slug": "music_producer",  "name": "음악 프로듀서","role": "music_producer",  "avatar": "🎵", "voice": "음악제작 부서 대표. 가사·스타일을 받아 곡을 만든다.", "department": "music"},
]


DEPARTMENTS = [
    {"slug": "music", "name": "음악제작", "status": "piloting", "description": "Mureka API로 가사+스타일을 곡으로 만드는 부서."},
]


def run_seed() -> None:
    db = SessionLocal()
    try:
        # 부서
        dept_map: dict[str, int] = {}
        for d in DEPARTMENTS:
            row = db.query(Department).filter_by(slug=d["slug"]).first()
            if not row:
                row = Department(**d)
                db.add(row)
                db.flush()
            dept_map[d["slug"]] = row.id

        # 직원
        for a in AGENTS:
            data = dict(a)
            dept_slug = data.pop("department", None)
            row = db.query(Agent).filter_by(slug=data["slug"]).first()
            if row:
                # 이름/아바타/voice는 갱신 (개발 중 오타 수정 반영)
                row.name = data["name"]
                row.role = data["role"]
                row.voice = data.get("voice", row.voice)
                row.avatar = data.get("avatar", row.avatar)
            else:
                row = Agent(**data)
                db.add(row)
                db.flush()
            if dept_slug:
                row.department_id = dept_map.get(dept_slug)
        db.commit()
    finally:
        db.close()
