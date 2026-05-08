#!/usr/bin/env bash
# Lucky HQ — Claude Code on the web SessionStart 훅
#
# 목적: 원격 세션이 켜지자마자 Python 의존성을 설치해서
#       곧바로 lint/import/실행이 가능하도록 한다.
#
# 로컬(개발자 PC) 세션에서는 아무것도 하지 않는다 (CLAUDE_CODE_REMOTE 체크).
# 멱등 — 여러 번 돌려도 안전.
set -euo pipefail

# 로컬 세션은 스킵
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-/home/user/lucky_hq}"

PIP_FLAGS="--quiet --root-user-action=ignore --disable-pip-version-check"

# pip 업그레이드 (조용히)
python3 -m pip install $PIP_FLAGS --upgrade pip >/dev/null 2>&1 || true

# requirements 설치 — 컨테이너 캐시 활용 (ci 대신 install)
if [ -f requirements.txt ]; then
  python3 -m pip install $PIP_FLAGS -r requirements.txt
fi

# PYTHONPATH에 레포 루트를 박아 두면 backend.app.* 임포트가 어디서나 동작
echo 'export PYTHONPATH="${PYTHONPATH:-}:'"$(pwd)"'"' >> "${CLAUDE_ENV_FILE:-/dev/null}"

echo "Lucky HQ deps ready ($(python3 -c 'import fastapi,sqlalchemy,httpx,PIL; print(f"fastapi={fastapi.__version__} sqlalchemy={sqlalchemy.__version__}")'))"
