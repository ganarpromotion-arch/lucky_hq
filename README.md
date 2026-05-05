# 🍀 Lucky HQ — AI 운영본부 V1

> Thin Code, Thick Agents.
> 코드는 안전·실행·감사만, 판단·생성은 AI에게.

## 무엇이 들어있나

V1 골격:

- **회사 본부 골격**: FastAPI + SQLModel + Postgres
- **직원 모듈 시스템 (플러그인)**: `backend/agents/builtins/<slug>.py` 한 파일 = 직원 한 종류
- **직원 추가 마법사**:
  - 자연어로 "이런 직원이 필요해"라고 적으면 Commander가 카탈로그에서 모듈을 추천
  - 모듈의 `config_schema`로 동적 폼이 그려짐
  - 생성 즉시 ▶ 일하기 가능 / cron 있으면 자동 실행
- **3패널 콘솔**: 좌(직원 리스트) · 중(오피스 + Job 결과) · 우(실시간 로그)
- **빌트인 직원 2명**:
  - `issue_scout` — 키워드 기반 이슈 탐색·보고 (T1, 매일 09시)
  - `writer` — 블로그/쇼츠/릴스/틱톡 대본 (T2, 수동)
- **LLM 라우팅 3티어** (메모리 정책 그대로):
  - T0 = 무료 (Groq/Gemini Flash) → 키 있으면 사용
  - T1 = Haiku — 기본
  - T2 = Sonnet — 글쓰기·전략·코드만
- **Job 기록 + AuditLog**: 모든 실행은 DB에 남음 (한 클릭 롤백 기반)

V2부터 추가될 것:
- 추가 빌트인 직원 (쇼츠 메이커, 음악 메이커, 텔레그램 봇, 주문배송, 마케팅검수 …)
- 디렉티브 시스템 UI (텔레그램 한 줄 → enforcement 4단계)
- 멀티 사용자 + 텔레그램 owner 승인
- 픽셀 오피스 애니메이션
- API 관리 직원의 키 격리 강화 (현재는 `core/llm.py`가 단독 접근)

---

## 폴더 구조

```
lucky-hq/
├── backend/
│   ├── main.py                    # FastAPI 진입점, lifespan으로 부트
│   ├── db.py                      # Postgres 연결 (URL 자동 정규화)
│   ├── models.py                  # Company/User/Department/Agent/Directive/Job/AuditLog
│   ├── api/
│   │   ├── agents.py              # 직원 CRUD + 모듈 카탈로그 + 일하기 + Job
│   │   └── commander.py           # 자연어 → 모듈 추천
│   ├── core/
│   │   ├── llm.py                 # T0/T1/T2 단일 인터페이스 (외부 키 격리)
│   │   └── scheduler.py           # APScheduler 래퍼 (Asia/Seoul)
│   └── agents/
│       ├── base.py                # AgentModule ABC
│       ├── registry.py            # @register 데코레이터 + 자동 로드
│       └── builtins/
│           ├── issue_scout.py     # 빌트인: 이슈 탐색
│           └── writer.py          # 빌트인: 글쓰기/대본
├── frontend/
│   ├── index.html                 # 3패널 + 마법사 모달
│   ├── styles.css                 # 다크 테마
│   ├── app.js                     # 직원 리스트/실행/로그
│   └── wizard.js                  # 마법사 흐름
├── requirements.txt
├── railway.json
├── Procfile
├── .env.example
├── .gitignore
└── README.md
```

---

## 배포 (Railway)

레일웨이 프로젝트는 이미 세팅돼 있다고 했으니, 파일만 GitHub `lucky-hq` 레포 main 브랜치에 올리면 자동 배포됩니다.

### 1. GitHub에 올리기

이 zip을 풀고 `lucky-hq/` 안에서:

```bash
cd lucky-hq
git init
git add -A
git commit -m "feat: Lucky HQ V1 — 본부 골격 + 직원 추가 마법사"
git branch -M main
git remote add origin https://github.com/<유저명>/lucky-hq.git
git push -u origin main
```

> ⚠️ 토큰을 명령줄이나 채팅에 절대 붙여넣지 마세요. `git push` 시 GitHub가 인증 프롬프트를 띄우면, 거기서만 PAT를 입력합니다 (또는 GitHub CLI / SSH 키 사용).

### 2. Railway Variables (대시보드에서 직접 입력)

| 변수 | 값 |
|---|---|
| `DATABASE_URL` | (Postgres 플러그인이 자동 주입) |
| `ANTHROPIC_API_KEY` | sk-ant-... |
| `GEMINI_API_KEY` | (선택, T0용) |
| `GROQ_API_KEY` | (선택, T0용) |
| `TZ` | Asia/Seoul |

`ANTHROPIC_API_KEY`만 있으면 V1은 동작합니다.

### 3. 헬스체크

배포 도메인 + `/health` → `{"ok": true}` 가 떠야 정상.

---

## 직원 한 명 한 명 만드는 흐름

### A. 마법사로 (사이트에서)

1. 콘솔 우상단 **+ 직원 추가** 클릭
2. 1단계 — "매일 아침에 AI 관련 이슈 5개 찾아서 정리해주는 직원" 같은 자연어 입력 → **추천 받기**
3. 2단계 — Commander가 추천한 모듈이 자동 선택됨. 이름·키워드·언어·스케줄(`0 9 * * *` 등) 채우고 **생성**
4. 좌측 직원 목록에 카드가 뜸. **▶ 일하기** 누르면 즉시 실행, cron이 있으면 정해진 시간에 자동 실행

### B. 새 직원 타입(모듈)을 추가하려면

`backend/agents/builtins/<slug>.py` 하나만 만들면 됩니다. 예시:

```python
from ..base import AgentModule
from ..registry import register
from backend.core.llm import call_llm

@register
class ShortsScripter(AgentModule):
    slug = "shorts_scripter"
    label = "쇼츠 대본 직원 (전문)"
    description = "쇼츠/릴스 전용으로 훅을 강하게 가져가는 대본을 씁니다."
    config_schema = [
        {"key": "niche", "label": "분야", "type": "text", "required": True},
        {"key": "duration", "label": "분량(초)", "type": "number", "default": 30},
    ]
    default_role_prompt = "너는 쇼츠 전문 작가다..."
    default_llm_tier = "T2"

    def do_work(self, agent, db, ctx):
        cfg = agent.config or {}
        prompt = f"{cfg.get('niche')} 분야의 {cfg.get('duration')}초 쇼츠 대본..."
        text = call_llm(tier=agent.llm_tier, system=agent.role_prompt, user=prompt)
        return {"script": text, "niche": cfg.get("niche")}
```

파일을 추가하고 push만 하면 다음 부팅 때 자동으로 카탈로그에 등록되고 마법사에서 선택할 수 있게 됩니다. **코드 한 파일 = 직원 한 종류**.

---

## 로컬에서 돌리기

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # ANTHROPIC_API_KEY만 채우면 됨 (DATABASE_URL은 비워두면 sqlite 사용)
uvicorn backend.main:app --reload
# http://localhost:8000
```

---

## 보안 메모 (중요)

- **외부 API 키는 `core/llm.py` 단 한 군데에서만 읽습니다.** 다른 모듈/직원은 provider 이름조차 모르고 `tier` 만 안다는 게 메모리 정책입니다.
- **토큰을 코드/채팅/이슈에 붙여넣지 마세요.** Railway/GitHub의 환경변수 입력란에만 직접 입력합니다.
- 위험한 작업(예: 외부 사이트 변경, 결제, 발송)은 V2에서 텔레그램 owner 승인 게이트를 추가합니다. 현재 빌트인 직원은 모두 **읽기·생성만** 합니다.

---

## 다음 단계 (제안)

1. 배포 확인 → 마법사로 `issue_scout` 직원 1명 만들고 ▶ 일하기 테스트
2. `writer`로 위 결과를 받아 블로그/쇼츠 대본 만들기 (수동 토픽 전달)
3. 이상 없으면 다음 빌트인 모듈을 한 명씩 추가:
   - `shorts_video_maker` (대본 → 쇼츠 영상; 외부 API)
   - `music_maker` (Suno/Udio 등)
   - `telegram_listener` (디렉티브 진입)
   - `approver` (위험 작업 승인 라우팅)
