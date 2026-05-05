"""DB 연결. Railway Postgres URL을 SQLModel/psycopg가 이해하는 형태로 정규화."""
import os
from sqlmodel import create_engine, Session, SQLModel

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local.db")

# Railway는 보통 'postgresql://...' 또는 'postgres://...' 로 줌.
# SQLModel/SQLAlchemy 2.x + psycopg3 조합은 'postgresql+psycopg://' 를 선호.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def init_db() -> None:
    """V1: SQLModel.metadata로 테이블 생성. V2에서 Alembic으로 교체 예정."""
    # 모든 모델을 임포트해서 metadata에 등록되도록
    from backend import models  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
