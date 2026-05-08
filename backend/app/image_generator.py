"""
영상 표지 이미지 생성기 (image_generator)

여러 외부 이미지 API를 동시에 호출해서 시안 후보를 생성한다.
사이트에서 사용자가 시안 중 하나를 골라 영상 인코딩에 사용.

지원 provider (등록된 키만 호출):
  - openai      : gpt-image-1 / DALL-E 3
  - gemini      : Imagen 3
  - stability   : SD3 Core / Ultra
  - pil         : 기존 PIL 폴백 (항상 사용 가능, 키 없을 때 보장)

각 provider는 곡 1개당 PNG 1장을 생성하고 WORK_DIR/job_X/ 아래에 저장한다.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .api_manager import call_api, _resolve_secret
from .config import get_settings
from .models import Setting
from . import video_maker as _vm

log = logging.getLogger("lucky_hq.image_gen")


@dataclass
class ImageProposal:
    job_id: int
    proposal_id: str         # "openai_1714929830" 같은 식별자
    provider: str            # openai | gemini | stability | pil
    image_path: str          # 디스크 경로
    image_url: str           # /api/music/archive/proposal-image/{job_id}/{proposal_id}
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "proposal_id": self.proposal_id,
            "provider": self.provider,
            "image_url": self.image_url,
            "error": self.error,
        }


def _build_prompt(title: str, mood: str, issue: str, style: str) -> str:
    """곡 정보 → 이미지 프롬프트 (영어).
    Imagen/DALL-E 모두 영어 프롬프트가 결과 품질이 가장 좋다."""
    mood_en = {
        "warm": "warm sunset golden hour atmosphere",
        "bright": "bright daylight cheerful",
        "cold": "cold winter blue tones",
        "fresh": "fresh spring green nature",
        "emotional": "moody emotional purple lavender",
        "energetic": "energetic vibrant pink magenta",
        "cozy": "cozy beige warm camel",
        "calm": "calm soft pastel grey blue",
        "dreamy": "dreamy ethereal twilight purple",
        "playful": "playful pop colorful",
        "modern": "modern minimal clean",
    }.get((mood or "modern").lower(), "modern atmospheric")

    parts = [
        f"Vertical 9:16 album cover art for a song titled \"{title}\"" if title else "Vertical 9:16 album cover art",
        f"theme: {issue}" if issue else "",
        f"style cue: {style}" if style else "",
        f"visual mood: {mood_en}",
        "no text, no logo, no watermark, no captions",
        "cinematic lighting, painterly, magazine cover quality",
    ]
    prompt = ". ".join(p for p in parts if p)
    return prompt[:1000]


def _registered_providers(db: Session) -> list[str]:
    """현재 등록된 이미지 API provider 목록 (DB Setting + env)."""
    settings = get_settings()
    providers: list[str] = []
    if _resolve_secret(db, "openai_api_key", settings.openai_api_key):
        providers.append("openai")
    if _resolve_secret(db, "gemini_api_key", settings.gemini_api_key):
        providers.append("gemini")
    if _resolve_secret(db, "stability_api_key", getattr(settings, "stability_api_key", "")):
        providers.append("stability")
    return providers


def _save_b64_png(b64: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode(b64)
    out_path.write_bytes(raw)
    return out_path


def _save_url_png(url: str, out_path: Path) -> Path:
    """URL → 다운로드해서 저장 (DALL-E 3 url 응답용)."""
    import httpx
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    return out_path


async def _gen_openai(db: Session, job_id: int, prompt: str,
                     out_dir: Path, ts: int) -> ImageProposal:
    proposal_id = f"openai_{ts}"
    out_path = out_dir / f"{proposal_id}.png"
    result = await call_api(
        db, provider="openai_image", operation="generate",
        payload={"prompt": prompt, "size": "1024x1536", "model": "gpt-image-1"},
        requester="video_editor", timeout=120.0,
    )
    if not result.get("ok"):
        # gpt-image-1 미지원 계정이면 dall-e-3로 폴백
        result = await call_api(
            db, provider="openai_image", operation="generate",
            payload={"prompt": prompt, "size": "1024x1792", "model": "dall-e-3",
                     "response_format": "b64_json"},
            requester="video_editor", timeout=120.0,
        )
    if not result.get("ok"):
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="openai",
                             image_path="", image_url="", error=result.get("error", "")[:200])
    data = result.get("data") or {}
    items = data.get("data") or []
    if not items:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="openai",
                             image_path="", image_url="", error="응답에 이미지 없음")
    item = items[0]
    try:
        if item.get("b64_json"):
            _save_b64_png(item["b64_json"], out_path)
        elif item.get("url"):
            _save_url_png(item["url"], out_path)
        else:
            return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="openai",
                                 image_path="", image_url="", error="b64/url 없음")
    except Exception as e:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="openai",
                             image_path="", image_url="", error=str(e)[:200])
    return ImageProposal(
        job_id=job_id, proposal_id=proposal_id, provider="openai",
        image_path=str(out_path),
        image_url=f"/api/music/archive/proposal-image/{job_id}/{proposal_id}",
    )


async def _gen_gemini(db: Session, job_id: int, prompt: str,
                     out_dir: Path, ts: int) -> ImageProposal:
    proposal_id = f"gemini_{ts}"
    out_path = out_dir / f"{proposal_id}.png"
    result = await call_api(
        db, provider="gemini_image", operation="generate",
        payload={"prompt": prompt, "aspect_ratio": "9:16",
                 "model": "imagen-3.0-generate-002"},
        requester="video_editor", timeout=120.0,
    )
    if not result.get("ok"):
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="gemini",
                             image_path="", image_url="", error=result.get("error", "")[:200])
    data = result.get("data") or {}
    preds = data.get("predictions") or []
    if not preds:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="gemini",
                             image_path="", image_url="", error="응답에 이미지 없음")
    b64 = (preds[0] or {}).get("bytesBase64Encoded")
    if not b64:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="gemini",
                             image_path="", image_url="", error="b64 없음")
    try:
        _save_b64_png(b64, out_path)
    except Exception as e:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="gemini",
                             image_path="", image_url="", error=str(e)[:200])
    return ImageProposal(
        job_id=job_id, proposal_id=proposal_id, provider="gemini",
        image_path=str(out_path),
        image_url=f"/api/music/archive/proposal-image/{job_id}/{proposal_id}",
    )


async def _gen_stability(db: Session, job_id: int, prompt: str,
                        out_dir: Path, ts: int) -> ImageProposal:
    proposal_id = f"stability_{ts}"
    out_path = out_dir / f"{proposal_id}.png"
    result = await call_api(
        db, provider="stability", operation="generate",
        payload={"prompt": prompt, "aspect_ratio": "9:16", "model": "core"},
        requester="video_editor", timeout=120.0,
    )
    if not result.get("ok"):
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="stability",
                             image_path="", image_url="", error=result.get("error", "")[:200])
    data = result.get("data") or {}
    b64 = data.get("image_b64")
    if not b64:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="stability",
                             image_path="", image_url="", error="이미지 데이터 없음")
    try:
        _save_b64_png(b64, out_path)
    except Exception as e:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="stability",
                             image_path="", image_url="", error=str(e)[:200])
    return ImageProposal(
        job_id=job_id, proposal_id=proposal_id, provider="stability",
        image_path=str(out_path),
        image_url=f"/api/music/archive/proposal-image/{job_id}/{proposal_id}",
    )


def _gen_pil(job_id: int, title: str, mood: str, issue: str,
            out_dir: Path, ts: int, seed: int) -> ImageProposal:
    """폴백: 기존 PIL 그라데이션 표지. API 키 없을 때도 작동 보장."""
    proposal_id = f"pil_{ts}_{seed}"
    out_path = out_dir / f"{proposal_id}.png"
    try:
        _vm.make_thumbnail(out_path, title=title, mood=mood,
                           subtitle=issue, seed=seed)
    except Exception as e:
        return ImageProposal(job_id=job_id, proposal_id=proposal_id, provider="pil",
                             image_path="", image_url="", error=str(e)[:200])
    return ImageProposal(
        job_id=job_id, proposal_id=proposal_id, provider="pil",
        image_path=str(out_path),
        image_url=f"/api/music/archive/proposal-image/{job_id}/{proposal_id}",
    )


async def generate_proposals(db: Session, job_id: int, title: str,
                             mood: str, issue: str, style: str = "",
                             include_pil: bool = True) -> list[ImageProposal]:
    """등록된 모든 이미지 API + PIL 폴백으로 시안 1장씩 생성.

    동시 호출. 각 provider는 키가 등록되어 있을 때만 시도된다.
    PIL 폴백은 항상 1장 추가 (키 전부 실패해도 시안은 보장).
    """
    out_dir = _vm.WORK_DIR / f"job_{job_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = _build_prompt(title=title, mood=mood, issue=issue, style=style)
    ts = int(time.time() * 1000) % 1_000_000_000

    providers = _registered_providers(db)
    tasks = []
    if "openai" in providers:
        tasks.append(_gen_openai(db, job_id, prompt, out_dir, ts))
    if "gemini" in providers:
        tasks.append(_gen_gemini(db, job_id, prompt, out_dir, ts))
    if "stability" in providers:
        tasks.append(_gen_stability(db, job_id, prompt, out_dir, ts))

    proposals: list[ImageProposal] = []
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, ImageProposal):
                if r.image_path:
                    proposals.append(r)
                else:
                    log.warning(f"image gen failed ({r.provider}): {r.error}")
            else:
                log.warning(f"image gen exception: {r}")

    # PIL 폴백 — API 시안이 없거나 옵션으로 추가
    if include_pil or not proposals:
        # PIL은 빠르니까 2장 (서로 다른 seed) 추가
        for s in (ts % 99991, (ts + 17) % 99991):
            p = _gen_pil(job_id, title, mood, issue, out_dir, ts, s)
            if p.image_path:
                proposals.append(p)

    return proposals


def get_proposal_path(job_id: int, proposal_id: str) -> Path | None:
    """저장된 시안 PNG 경로 (없으면 None)."""
    p = _vm.WORK_DIR / f"job_{job_id}" / f"{proposal_id}.png"
    return p if p.exists() else None
