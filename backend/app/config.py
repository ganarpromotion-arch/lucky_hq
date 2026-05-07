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
    timezone: str = "Asia/Seoul"
    timezone: str = "Asia/Seoul"
    timezone: str = "Asia/Seoul"

    # DB
    database_url: str = "sqlite:///./lucky_hq.db"

    # 외부 API (API 관리 직원만 직접 접근)
    mureka_api_key: str = ""
    mureka_base_url: str = "https://api.mureka.ai"

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # 작곡가 직원 — 가사 생성 LLM 모델
    # 우선순위: gemini (무료, 빠름) → anthropic → openai → 룰 폴백
    songwriter_llm_provider: str = "gemini"   # gemini | anthropic | openai
    songwriter_llm_model: str = "gemini-2.5-flash"

    # 텔레그램 — 곡 보고 + 채택 답장 webhook
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_api_base: str = "https://api.telegram.org"


@lru_cache
def get_settings() -> Settings:
    return Settings()
