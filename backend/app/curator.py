"""
큐레이터 직원 (curator)

매일 본부가 만들 N개 곡의 '이슈 시드'를 결정한다.

V1: DB의 issue_pool 테이블 또는 in-code 풀에서 랜덤 N개.
V1.5: 외부 트렌드 API (네이버 데이터랩 / Google Trends KR) 연결.

진솔정 + 계절 + 일상 카테고리 균형 잡힌 풀.
"""
from __future__ import annotations
import random
from datetime import datetime


# 한국어 이슈 풀 — 작곡가가 받았을 때 다양한 무드 분포가 나오도록 설계
ISSUE_POOL: list[str] = [
    # 진솔정 도메인
    "진솔정 가을 메뉴 곱창전골",
    "진솔정 한 잔의 막걸리",
    "진솔정 가게 앞 골목의 저녁",
    "곱창전골 한 점의 위로",
    "주말 저녁 친구들과 진솔정",

    # 계절·날씨 (현재 5월 기준 가까운 무드 위주)
    "오월의 햇살 가득한 오후",
    "초여름 바람이 부는 거리",
    "비 오는 날 창가의 커피",
    "초록이 짙어지는 5월",
    "장마 시작 전 마지막 푸른 날",

    # 일상·감정
    "월요일 아침의 출근길",
    "퇴근 후 마시는 한 잔",
    "오랜만에 만난 친구와 산책",
    "주말 늦은 아침의 여유",
    "혼자 듣는 새벽의 라디오",

    # 응원·도전
    "힘든 한 주를 버텨낸 너에게",
    "다시 시작하는 용기",
    "포기하지 않는 마음",

    # 사랑·그리움
    "오래전 그 사람 생각",
    "닿지 못한 마음 한 줄",

    # 도시·풍경
    "한강을 따라 걷는 저녁",
    "야경이 아름다운 옥상",
    "지하철 막차의 풍경",

    # 음식·여행
    "동네 단골 카페의 아메리카노",
    "주말 짧은 여행의 첫 새벽",
]


def pick_issues(n: int = 6, seed: int | None = None) -> list[str]:
    """이슈 풀에서 중복 없이 n개 뽑기.
    seed 주면 결정적, 없으면 매번 랜덤."""
    rng = random.Random(seed) if seed is not None else random.Random()
    pool = list(ISSUE_POOL)
    rng.shuffle(pool)
    return pool[: max(1, min(n, len(pool)))]


def today_seed() -> int:
    """오늘 날짜 기반 결정적 시드 (같은 날 재실행해도 같은 이슈 나옴)."""
    return int(datetime.now().strftime("%Y%m%d"))
