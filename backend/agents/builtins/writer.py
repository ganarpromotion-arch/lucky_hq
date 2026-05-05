"""글쓰기/대본 직원.

블로그 글, 쇼츠/릴스/틱톡 대본을 생성. 글쓰기는 메모리 정책상 T2(Sonnet) 기본.
ctx에 topic이 들어오면 그걸 우선, 없으면 config.default_topic 사용.
"""
from ..base import AgentModule
from ..registry import register
from backend.core.llm import call_llm


FORMAT_HINTS = {
    "blog": "블로그 글 (도입-본문 3섹션-결론, 800~1200자, 자연스러운 한국어)",
    "shorts_script": "유튜브 쇼츠 대본 (45초 분량, 훅-본문-CTA, 화면지시 포함)",
    "reels_script": "인스타 릴스 대본 (30초, 강한 훅, 자막용 짧은 문장)",
    "tiktok_script": "틱톡 대본 (15~30초, 트렌디한 어조, 후크 강조)",
}


@register
class Writer(AgentModule):
    slug = "writer"
    label = "글쓰기 / 대본 직원"
    description = "주어진 주제로 블로그 글 또는 쇼츠·릴스·틱톡 대본을 작성합니다."

    config_schema = [
        {
            "key": "format",
            "label": "형식",
            "type": "select",
            "options": list(FORMAT_HINTS.keys()),
            "default": "blog",
        },
        {
            "key": "tone",
            "label": "톤",
            "type": "text",
            "default": "친근하고 명확하게",
        },
        {
            "key": "default_topic",
            "label": "기본 주제 (ctx에 topic 안 들어올 때 사용)",
            "type": "text",
            "default": "오늘의 이슈",
        },
    ]

    default_role_prompt = (
        "너는 글쓰기 직원이다. 주어진 주제·형식·톤에 맞게 한국어로 매끄럽게 작성한다. "
        "과장하지 않고, 사실 검증이 필요한 부분은 '확인 필요'로 표시한다."
    )
    # 글쓰기는 정책상 T2 (Sonnet)
    default_llm_tier = "T2"

    def do_work(self, agent, db, ctx: dict) -> dict:
        cfg = agent.config or {}
        topic = (ctx or {}).get("topic") or cfg.get("default_topic", "오늘의 이슈")
        fmt = cfg.get("format", "blog")
        tone = cfg.get("tone", "친근하고 명확하게")
        hint = FORMAT_HINTS.get(fmt, FORMAT_HINTS["blog"])

        prompt = (
            f"주제: {topic}\n"
            f"형식: {fmt} — {hint}\n"
            f"톤: {tone}\n"
            f"위 조건으로 작성해줘. 형식이 대본이면 화면 지시(괄호)도 포함해줘."
        )
        text = call_llm(
            tier=agent.llm_tier or self.default_llm_tier,
            system=agent.role_prompt or self.default_role_prompt,
            user=prompt,
            max_tokens=2000,
        )
        return {
            "format": fmt,
            "topic": topic,
            "tone": tone,
            "content": text,
        }
