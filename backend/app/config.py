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

    # DB
    database_url: str = "sqlite:///./lucky_hq.db"

    # 외부 API (API 관리 직원만 직접 접근)
    mureka_api_key: str = ""
    mureka_base_url: str = "https://api.mureka.ai"

    # Mureka 곡 생성 옵션 (DB Setting으로 덮어쓰기 가능)
    # model: V7.6 ($0.03/song) → "auto" 또는 "mureka-7.5" / V8/V9 ($0.045/song) → "mureka-v8" | "mureka-v9"
    # n: 한 번에 생성할 곡 수 (Mureka 기본 2, 최대 3)
    # max_duration_sec: 곡 길이 상한 (Mureka 한도 5m30s = 330초)
    mureka_model: str = "auto"
    mureka_n: int = 2
    mureka_max_duration_sec: int = 330

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com"

    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"

    # Stability AI (SD3 / Core / Ultra) — 영상 표지 이미지 생성용
    stability_api_key: str = ""

    # 작곡가 직원 — 가사 생성 LLM 모델
    # 우선순위: gemini (무료, 빠름) → anthropic → openai → 룰 폴백
    songwriter_llm_provider: str = "gemini"   # gemini | anthropic | openai
    songwriter_llm_model: str = "gemini-2.5-flash"

    # 텔레그램 — 곡 보고 + 채택 답장 webhook
    telegram_bot_token: str = ""
    telegram_owner_chat_id: str = ""
    telegram_webhook_secret: str = ""
    telegram_api_base: str = "https://api.telegram.org"

    # 채널 브랜딩 — 커버 워터마크 + 영어 설명 서명에 사용 (DB Setting으로 덮어쓰기 가능)
    channel_name: str = "Healing Waves"
    channel_handle: str = "@HealingWaves00"

    # YouTube 업로드 정책 (구현 예정)
    # IMPORTANT: 영상 설명(description)은 영어로만 작성한다.
    # 제목/태그는 곡의 언어를 따라가지만 description은 무조건 영어 — 글로벌 검색 노출 + 동일 톤 유지.
    # 구현 위치: 추가될 youtube_uploader.py / video_caption.py 에서 이 플래그를 강제 적용.
    youtube_description_language: str = "en"


@lru_cache
def get_settings() -> Settings:
    return Settings()
