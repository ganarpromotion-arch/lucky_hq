"""이슈 탐색 직원.

V1: LLM에게 키워드 기반 최신 이슈 정리를 시킴 (실제 웹 검색은 V2에서 별도 직원으로).
보고 형식: 헤드라인 + 3줄 요약 + 출처(있으면).
"""
from ..base import AgentModule
from ..registry import register
from backend.core.llm import call_llm


@register
class IssueScout(AgentModule):
    slug = "issue_scout"
    label = "이슈 탐색 직원"
    description = "키워드 기반으로 최근 이슈/트렌드를 찾아 헤드라인+요약+출처로 보고합니다."

    config_schema = [
        {
            "key": "keywords",
            "label": "관심 키워드 (쉼표 구분)",
            "type": "text",
            "required": True,
            "placeholder": "AI, 반도체, 한국 스타트업",
        },
        {
            "key": "language",
            "label": "언어",
            "type": "select",
            "options": ["ko", "en"],
            "default": "ko",
        },
        {
            "key": "max_items",
            "label": "한 번에 가져올 이슈 수",
            "type": "number",
            "default": 5,
        },
    ]

    default_role_prompt = (
        "너는 이슈 탐색 직원이다. 주어진 키워드로 최근 이슈를 추리고 "
        "각 이슈에 대해 한 줄 헤드라인 + 3줄 요약 + 출처(추정 포함)로 보고한다. "
        "확실하지 않으면 '검증 필요'라고 명시한다."
    )
    default_llm_tier = "T1"
    default_schedule_cron = "0 9 * * *"  # 매일 오전 9시

    def do_work(self, agent, db, ctx: dict) -> dict:
        cfg = agent.config or {}
        keywords = (cfg.get("keywords") or ctx.get("keywords") or "").strip()
        max_items = int(cfg.get("max_items", 5))
        language = cfg.get("language", "ko")

        if not keywords:
            return {"error": "keywords 미설정", "report": ""}

        prompt = (
            f"키워드: {keywords}\n"
            f"언어: {language}\n"
            f"최근 이슈 {max_items}건을 다음 형식으로 정리해줘:\n"
            f"1. [헤드라인]\n   요약: ...\n   출처: ... (불확실하면 '검증 필요')\n"
        )
        text = call_llm(
            tier=agent.llm_tier or self.default_llm_tier,
            system=agent.role_prompt or self.default_role_prompt,
            user=prompt,
            max_tokens=1200,
        )
        return {
            "report": text,
            "keywords": keywords,
            "language": language,
            "requested_count": max_items,
        }
