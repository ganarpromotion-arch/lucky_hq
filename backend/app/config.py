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

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"

    # 작곡가 직원 — 가사 생성 LLM 모델
    # claude-haiku-4-5-20251001 (빠르고 저렴) / claude-sonnet-4-6 (더 자연스러움)
    songwriter_llm_provider: str = "anthropic"   # anthropic | openai
    songwriter_llm_model: str = "claude-haiku-4-5-20251001"

    # 큐레이터 직원 — 일일 이슈/컨셉 큐레이션 LLM
    curator_llm_model: str = "claude-haiku-4-5-20251001"

    # 일일 배치 스케줄
    batch_enabled: bool = True
    batch_schedule_hour: int = 9        # 매일 KST 09:00 시작
    batch_schedule_minute: int = 0
    batch_timezone: str = "Asia/Seoul"
    daily_song_count: int = 10
    batch_review_minutes: int = 60      # 검토창 (이후 자동 finalize)
    batch_song_timeout_seconds: int = 600  # 곡 한 개의 Mureka 처리 한도

    # 자동 잔량 임계 (Mureka 잔량 — 단위는 Mureka가 주는 그대로)
    mureka_min_balance_threshold: float = 1.0  # 이보다 적으면 배치 skip + 알림

    # 데이터 디렉토리 (Railway Volume 권장: /data, 없으면 ./data)
    data_dir: str = "/data"

    # 텔레그램 (v1.5에서 활성화 예정, 자리만 마련)
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
