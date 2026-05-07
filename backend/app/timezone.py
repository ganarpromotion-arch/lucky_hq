"""
Lucky HQ 시간 헬퍼

본부는 한국 회사 + 운영자 모두 KST → DB에 KST naive datetime 저장.
모든 datetime.utcnow() 호출은 now_kst()로 대체.
"""
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    """현재 시각 (KST, naive datetime — DB 저장용).
    SQLAlchemy의 default=callable은 naive를 기대."""
    return datetime.now(KST).replace(tzinfo=None)


def format_kst(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """datetime → 한국시간 문자열."""
    if dt is None:
        return ""
    return dt.strftime(fmt)


def to_iso_kst(dt: datetime | None) -> str:
    """API 응답용 ISO 문자열 (+09:00 명시).
    JS Date에서 파싱하면 자동으로 사용자 로컬에 맞게 표시됨."""
    if dt is None:
        return ""
    # naive면 KST로 가정
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.isoformat()
