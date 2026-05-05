"""
Lucky HQ 환경설정
- Postgres 연결, 외부 API 키 모두 env에서 로드
- 코드/로그/커밋에 키가 절대 들어가지 않도록 유지
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 앱
    app_name: str = "Lucky HQ"
    app_env: str = "development"

    # DB
    database_url: str = "sqlite:///./lucky_hq.db"

    # 외부 API (API 관리 직원만 직접 접근)
    mureka_api_key: str = ""
    mureka_base_url: str = "https://api.mureka.ai"

    # 텔레그램 (v1.5에서 활성화 예정, 자리만 마련)
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
