# 🍀 Lucky HQ — AI 운영본부 V1

> Thin Code, Thick Agents. 코드는 안전·실행·감사만, 판단·생성은 AI에게.

## 무엇이 들어있나

V1 골격:
- **메인 콘솔 (3패널)**: 좌 직원·하는일 / 중 픽셀 오피스 / 우 실시간 로그
- **직원 12명 시드**: 지휘관·관제·텔레그램·API관리·블로그검증·쇼츠검증·주문배송·마케팅검수·글쓰기검수·개발테스트·학습·승인 + 음악 프로듀서
- **음악제작 부서 (첫 부서)**: Mureka API로 가사+스타일 → 곡 생성

## 설계 원칙

1. **모든 외부 API는 `api_manager.call_api()` 단일 인터페이스로만 호출**
   - 다른 직원/부서는 provider 이름만 안다 (예: `"mureka"`)
   - API 키/시크릿은 환경변수에만 존재. 코드·로그·DB에 평문 저장 금지.
2. **모든 호출과 행동은 감사 기록**
   - `audit_logs` (직원 행동), `api_calls` (외부 호출 요약 — 키 redacted)
3. **부서는 단계 운영**: `proposed → piloting → active`
4. **직원 = AI 페르소나 + 행동 권한**
5. **부서 = 웹 프로그램 (service/) + AI 행동 레이어 (프롬프트·매니페스트)**

## 디렉토리

```
lucky_hq/
├── backend/
│   └── app/
│       ├── main.py              # FastAPI 진입점
│       ├── config.py            # 환경설정 (env-only)
│       ├── db.py                # SQLAlchemy 세션
│       ├── models.py            # Department / Agent / Job / AuditLog / ApiCall
│       ├── seed.py              # 부서·직원 초기 데이터
│       ├── api_manager.py       # ★ 외부 API 단일 진입점 (call_api)
│       └── routers/
│           ├── console.py       # /api/agents · /api/departments · /api/logs
│           └── music.py         # /api/music/generate · /api/music/jobs
├── frontend/
│   ├── index.html               # 메인 콘솔
│   ├── music.html               # 음악제작 부서
│   └── static/
│       ├── style.css
│       ├── console.js
│       └── music.js
├── Procfile
├── railway.json
└── requirements.txt
```

## 환경변수 (Railway)

| 변수 | 필수 | 설명 |
|------|-----|------|
| `DATABASE_URL`        | ✓ | Railway Postgres (자동 주입) |
| `MUREKA_API_KEY`      | ✓ | Mureka Bearer 토큰 |
| `MUREKA_BASE_URL`     |   | 기본 `https://api.mureka.ai` |
| `APP_ENV`             |   | `production` / `development` |
| `TELEGRAM_BOT_TOKEN`  |   | v1.5에서 사용 |
| `TELEGRAM_OWNER_CHAT_ID` |   | v1.5에서 사용 |

> **토큰은 채팅·코드·커밋·로그에 절대 들어가지 않습니다.**
> Railway 대시보드 → Variables 탭에서만 등록.

## 로컬 실행

```bash
cd lucky_hq
pip install -r requirements.txt

export DATABASE_URL="sqlite:///./lucky_hq.db"
export MUREKA_API_KEY="..."   # 로컬 개발용

uvicorn backend.app.main:app --reload
# → http://localhost:8000
```

## API

### 메인 콘솔
- `GET /api/health` — 헬스체크
- `GET /api/agents` — 직원 12명 + 현재 상태
- `GET /api/departments` — 부서 목록
- `GET /api/logs?limit=50` — 최근 감사 로그
- `GET /api/jobs/recent` — 최근 작업

### 음악제작 부서
- `POST /api/music/generate` — `{lyrics, style, title}` → Job 생성
- `GET /api/music/jobs` — 부서 작업 목록
- `GET /api/music/jobs/{id}` — 단건 상태 (Mureka 자동 폴링/갱신)

## V1.5 로드맵

- 텔레그램 지휘관 (자연어 명령 단일 진입점)
- LLM 라우팅 3티어 (Gemini Flash/Groq → Haiku → Sonnet)
- 디렉티브 시스템 (enforcement: hint/soft/hard/blocking, scope: global/site/department/agent)
- 멀티 사용자·역할 6단·자원 락
- 두 번째 부서 (블로그 또는 쇼츠 검증)

---
🍀 _Lucky Company · 럭키컴퍼니_
