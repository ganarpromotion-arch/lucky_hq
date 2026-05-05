"""
DB 세션 / 엔진
- Postgres URL은 Railway가 DATABASE_URL로 주입
- 로컬 개발은 SQLite로 폴백
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import get_settings

settings = get_settings()
url = settings.database_url

# Railway가 postgres:// 형태로 주는 경우 SQLAlchemy 호환 변환
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}

engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
