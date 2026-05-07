"""
DB 세션 / 엔진
- Postgres URL은 Railway가 DATABASE_URL로 주입
- 로컬 개발은 SQLite로 폴백
- 타임존: Asia/Seoul (한국시간)
"""
from sqlalchemy import create_engine, event
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


# Postgres 연결마다 세션 타임존을 KST로
if url.startswith("postgresql"):
    @event.listens_for(engine, "connect")
    def _set_kst_tz(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("SET TIME ZONE 'Asia/Seoul'")
        cur.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
