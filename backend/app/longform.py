"""
롱폼 수면·앰비언트 영상 엔진 (수익 구조상 가장 확실한 형태)

곡 1개(무가사 앰비언트) → 목표 길이(기본 1시간)로 루프한 가로 1920x1080 mp4.
- 커버: 차분한 가로 그라데이션 + 소프트 발광 + 최소 텍스트 (PIL, 무료·결정적)
- 인코딩: ffmpeg stream_loop 로 오디오를 목표 길이까지 반복 + 페이드 인/아웃
- 메타데이터: 유튜브 업로드용 영어 제목/설명/태그 (수동 업로드 시 복사)

정책 근거: 롱폼·단일 니치·큐레이션 = 'inauthentic 대량생산' 단속의 정반대.
RPM 근거: 수면/웰니스 $10+, 무가사라 글로벌 고RPM 시청자.
"""
from __future__ import annotations
import asyncio
import logging
import math
import random
import subprocess
from pathlib import Path

import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .db import SessionLocal
from .models import LongformRelease, Job, AuditLog
from . import archiver
from .video_maker import _load_font, _ffmpeg_path, _probe_duration, download_audio, WORK_DIR

log = logging.getLogger("lucky_hq.longform")

# 가로(16:9) 해상도 — 롱폼 표준
LW, LH = 1920, 1080

# 니치별 차분한 팔레트 (위 / 중간 / 아래) — 수면은 어둡고 낮은 채도
NICHE_PALETTES = {
    "sleep":     ((40, 55, 90), (25, 35, 65), (10, 14, 30)),      # 심야 남색 → 검푸름
    "study":     ((60, 75, 95), (40, 52, 72), (22, 30, 45)),      # 차분한 블루그레이
    "cinematic": ((70, 60, 95), (45, 38, 70), (18, 15, 35)),      # 보랏빛 시네마틱
}
DEFAULT_NICHE = "sleep"


def _gradient_h(niche: str, angle_deg: float = 115.0) -> Image.Image:
    """가로 화면용 3-stop 대각선 그라데이션 (차분·저채도)."""
    top, mid, bot = NICHE_PALETTES.get(niche, NICHE_PALETTES[DEFAULT_NICHE])
    img = Image.new("RGB", (LW, LH))
    px = img.load()
    rad = math.radians(angle_deg)
    dx, dy = math.sin(rad), math.cos(rad)
    projs = [x * dx + y * dy for x in (0, LW - 1) for y in (0, LH - 1)]
    proj_min, proj_max = min(projs), max(projs)
    span = max(1.0, proj_max - proj_min)

    def lerp(a, b, t):
        return int(a + (b - a) * t)

    for y in range(LH):
        for x in range(LW):
            t = ((x * dx + y * dy) - proj_min) / span
            if t < 0.5:
                u = t * 2
                r, g, b = (lerp(top[i], mid[i], u) for i in range(3))
            else:
                u = (t - 0.5) * 2
                r, g, b = (lerp(mid[i], bot[i], u) for i in range(3))
            px[x, y] = (r, g, b)
    return img


def _soft_bloom(img: Image.Image, niche: str, seed: int) -> Image.Image:
    """은은한 달빛/성운 같은 발광 1~2개 (차분하게, 상단)."""
    rng = random.Random(seed)
    top, _, _ = NICHE_PALETTES.get(niche, NICHE_PALETTES[DEFAULT_NICHE])
    glow = (min(top[0] + 60, 255), min(top[1] + 60, 255), min(top[2] + 70, 255))
    overlay = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    for _ in range(rng.randint(1, 2)):
        cx = rng.randint(LW // 4, 3 * LW // 4)
        cy = rng.randint(LH // 5, LH // 2)
        radius = rng.randint(LW // 5, LW // 3)
        bloom = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
        d = ImageDraw.Draw(bloom)
        d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                  fill=(glow[0], glow[1], glow[2], 70))
        bloom = bloom.filter(ImageFilter.GaussianBlur(radius=radius // 2))
        overlay = Image.alpha_composite(overlay, bloom)
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_ambient_cover(out_path: Path, title: str, subtitle: str = "",
                       niche: str = "sleep", seed: int | None = None,
                       watermark: str = "") -> Path:
    """가로 1920x1080 차분한 커버 1장 생성. watermark = 채널명(하단 서명)."""
    s = seed if seed is not None else random.randint(0, 10_000)
    rng = random.Random(s)
    img = _gradient_h(niche, angle_deg=105 + rng.uniform(0, 25))
    img = _soft_bloom(img, niche, seed=s)

    # 하단 비네트 (텍스트 가독성)
    vig = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    ImageDraw.Draw(vig).rectangle([(0, LH - 420), (LW, LH)], fill=(0, 0, 0, 70))
    img = Image.alpha_composite(img.convert("RGBA"), vig.filter(ImageFilter.GaussianBlur(60))).convert("RGB")

    draw = ImageDraw.Draw(img)
    title_font = _load_font(96)
    sub_font = _load_font(40)

    # 제목 (가운데 살짝 아래)
    t = (title or "Deep Sleep").strip()
    bbox = draw.textbbox((0, 0), t, font=title_font)
    tw = bbox[2] - bbox[0]
    tx, ty = (LW - tw) // 2, LH // 2 - 40
    draw.text((tx + 3, ty + 3), t, font=title_font, fill=(0, 0, 0))
    draw.text((tx, ty), t, font=title_font, fill=(240, 244, 252))

    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        sw = bbox[2] - bbox[0]
        draw.text(((LW - sw) // 2, ty + 130), subtitle, font=sub_font, fill=(190, 200, 220))

    # 채널 워터마크 (하단 중앙, 은은하게)
    if watermark:
        wm_font = _load_font(34)
        bbox = draw.textbbox((0, 0), watermark, font=wm_font)
        ww = bbox[2] - bbox[0]
        draw.text(((LW - ww) // 2, LH - 78), watermark, font=wm_font, fill=(150, 165, 195))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _draw_cover_text(img: Image.Image, title: str, subtitle: str = "", watermark: str = "") -> None:
    """커버 이미지 위에 제목/부제/워터마크 텍스트 오버레이 (하단 배치, 그림자로 가독성)."""
    draw = ImageDraw.Draw(img)
    title_font = _load_font(96)
    sub_font = _load_font(40)
    wm_font = _load_font(34)
    t = (title or "Deep Sleep").strip()
    bbox = draw.textbbox((0, 0), t, font=title_font)
    tw = bbox[2] - bbox[0]
    tx, ty = (LW - tw) // 2, int(LH * 0.60)
    draw.text((tx + 3, ty + 3), t, font=title_font, fill=(0, 0, 0))
    draw.text((tx, ty), t, font=title_font, fill=(245, 248, 255))
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
        sw = bbox[2] - bbox[0]
        draw.text(((LW - sw) // 2, ty + 120), subtitle, font=sub_font, fill=(205, 214, 230))
    if watermark:
        bbox = draw.textbbox((0, 0), watermark, font=wm_font)
        ww = bbox[2] - bbox[0]
        draw.text(((LW - ww) // 2, LH - 72), watermark, font=wm_font, fill=(200, 210, 230))


# 니치별 AI 커버 장면 프롬프트 (텍스트/사람 배제, 아늑·차분)
_SCENE_PROMPTS = {
    "sleep": ("cozy dark bedroom at night, large rain-streaked window, warm dim lamp glow, "
              "blurred soft city lights outside, moody peaceful sleepy atmosphere, cinematic, soft bokeh"),
    "study": ("cozy study desk by a rainy window at night, warm lamp, books and a small plant, "
              "soft focused calm atmosphere, cinematic, soft bokeh, warm tones"),
    "cinematic": ("serene misty mountain lake at dusk, soft glowing sky, calm reflective water, "
                  "cinematic wide landscape, moody peaceful atmosphere, soft light"),
}


async def make_ai_cover(db, out_path: Path, title: str, subtitle: str = "",
                        niche: str = "sleep", watermark: str = "",
                        scene_prompt: str = "") -> Path | None:
    """Stability 이미지로 16:9 분위기 커버 생성 → 제목/워터마크 오버레이. 실패 시 None."""
    from .api_manager import call_api
    prompt = (scene_prompt or _SCENE_PROMPTS.get(niche, _SCENE_PROMPTS["sleep"])) + ", no text, no people"
    try:
        r = await call_api(db, provider="stability", operation="generate",
                           payload={"prompt": prompt, "aspect_ratio": "16:9", "model": "core"},
                           requester="video_editor")
        b64 = (r.get("data") or {}).get("image_b64")
        if not r.get("ok") or not b64:
            log.info(f"AI 커버 미사용 (fallback): {r.get('error')}")
            return None
        img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
        img = ImageOps.fit(img, (LW, LH), Image.LANCZOS)
        # 하단 어둡게 (텍스트 가독성)
        ov = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
        ImageDraw.Draw(ov).rectangle([(0, LH - 380), (LW, LH)], fill=(0, 0, 0, 130))
        img = Image.alpha_composite(img.convert("RGBA"), ov.filter(ImageFilter.GaussianBlur(50))).convert("RGB")
        _draw_cover_text(img, title, subtitle, watermark)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        return out_path
    except Exception as e:
        log.warning(f"AI 커버 생성 실패 → PIL 폴백: {e}")
        return None


# ── 영어 메타데이터 (유튜브 수동 업로드용) ───────────────────
_NICHE_META = {
    "sleep": {
        "kw": ["sleep music", "relaxing music", "deep sleep", "calm music",
               "insomnia relief", "sleep meditation", "ambient sleep", "night music"],
        "line": "Drift into deep, restful sleep with this calming ambient soundscape.",
        "tail": "sleep, relax, or fall asleep faster",
    },
    "study": {
        "kw": ["study music", "focus music", "concentration", "deep focus",
               "lofi study", "work music", "productivity", "reading music"],
        "line": "Stay calm and focused with this gentle ambient study soundscape.",
        "tail": "study, work, or focus deeply",
    },
    "cinematic": {
        "kw": ["ambient music", "cinematic music", "relaxing ambient", "meditation music",
               "background music", "atmospheric", "calm", "peaceful music"],
        "line": "A calm cinematic ambient soundscape for rest and reflection.",
        "tail": "relax, meditate, or unwind",
    },
}


def build_metadata(theme: str, niche: str, target_min: int, title_hint: str = "",
                   channel_name: str = "", channel_handle: str = "") -> dict:
    """유튜브 업로드용 영어 제목/설명/태그. (정책: 설명은 항상 영어)"""
    meta = _NICHE_META.get(niche, _NICHE_META["sleep"])
    label = {"sleep": "Deep Sleep Music", "study": "Focus Study Music",
             "cinematic": "Ambient Music"}.get(niche, "Ambient Music")
    # 유튜브 검색 최적화 제목 (곡명 대신 키워드 중심, 전부 영어)
    hours = target_min // 60
    dur_txt = (f"{hours} Hour" + ("s" if hours > 1 else "")) if target_min >= 60 else f"{target_min} Min"
    yt_titles = {
        "sleep": f"Relaxing Sleep Music — Deep Sleep Piano, Insomnia & Stress Relief · {dur_txt}",
        "study": f"Relaxing Study Music — Piano for Focus, Concentration & Deep Work · {dur_txt}",
        "cinematic": f"Relaxing Ambient Music — Calm Cinematic Soundscape for Peace · {dur_txt}",
    }
    yt_title = yt_titles.get(niche, yt_titles["sleep"])[:100]

    # 채널 서명 (있으면 상단 브랜딩 + 구독 유도)
    sign = ""
    if channel_name:
        sub = f" · Subscribe: youtube.com/{channel_handle}" if channel_handle else ""
        sign = f"🌊 {channel_name}{sub}\n\n"

    desc = (
        f"{sign}"
        f"{meta['line']}\n\n"
        f"🎧 {target_min} minutes of original, AI-composed ambient music — "
        f"perfect to {meta['tail']}.\n"
        f"Use headphones and a low volume for the best experience.\n\n"
        f"This is original instrumental music created for relaxation. "
        f"No copyrighted material is used.\n\n"
        f"#{niche} #ambient #relaxingmusic"
    )
    tags = list(dict.fromkeys(meta["kw"] + [w.lower() for w in theme.split()[:4] if len(w) > 1]))
    return {"yt_title": yt_title, "yt_description": desc, "yt_tags": tags[:15]}


# ── 루프 인코딩 ──────────────────────────────────────────────
async def concat_audios(audio_paths: list[Path], out_path: Path,
                        crossfade_sec: float = 3.0) -> Path:
    """여러 오디오를 크로스페이드로 매끄럽게 이어붙여 하나의 시퀀스로.

    수면·앰비언트는 곡 전환이 부드러워야 하므로 acrossfade(겹쳐 넘김)를 체인으로 적용.
    곡이 1개면 그대로 복사.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [p for p in audio_paths if p and Path(p).exists()]
    if not paths:
        raise RuntimeError("이어붙일 오디오가 없음")
    if len(paths) == 1:
        import shutil as _sh
        _sh.copyfile(paths[0], out_path)
        return out_path

    ffmpeg = _ffmpeg_path()
    inputs: list[str] = []
    for p in paths:
        inputs += ["-i", str(p)]

    # [0][1]acrossfade=d=X[a1]; [a1][2]acrossfade=d=X[a2]; ...
    cf = f"acrossfade=d={crossfade_sec}:c1=tri:c2=tri"
    parts: list[str] = []
    prev = "[0]"
    for i in range(1, len(paths)):
        label = f"[a{i}]" if i < len(paths) - 1 else "[out]"
        parts.append(f"{prev}[{i}]{cf}{label}")
        prev = label
    filtergraph = ";".join(parts)

    cmd = [
        ffmpeg, "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat 실패 (code {proc.returncode}): "
                           f"{stderr.decode('utf-8', 'ignore')[-500:]}")
    return out_path


async def encode_loop_video(image_path: Path, audio_path: Path, out_path: Path,
                            target_sec: int = 3600, fps: int = 2) -> dict:
    """정지 커버 + 오디오 루프 → 목표 길이 mp4.

    -stream_loop -1 로 오디오를 무한 반복하고 -t 로 목표 길이에서 자른다.
    페이드 인(3s)/아웃(6s)로 시작·끝을 부드럽게.
    still image 라 저 fps + libx264 로 파일이 매우 작다 (오디오가 용량 대부분).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_path()
    fade_out_start = max(0, target_sec - 6)

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-framerate", str(fps), "-i", str(image_path),
        "-stream_loop", "-1", "-i", str(audio_path),
        "-t", str(target_sec),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k",
        "-af", f"afade=t=in:st=0:d=3,afade=t=out:st={fade_out_start}:d=6",
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg loop 실패 (code {proc.returncode}): "
                           f"{stderr.decode('utf-8', 'ignore')[-500:]}")
    duration = await _probe_duration(out_path)
    return {
        "video_path": str(out_path),
        "duration_sec": int(duration),
        "file_size": out_path.stat().st_size if out_path.exists() else 0,
    }


# ── 렌더 오케스트레이터 (백그라운드 태스크) ──────────────────
async def render_release(release_id: int) -> None:
    """LongformRelease 1건을 렌더: 오디오 확보 → 커버 → 루프 인코딩 → DB 갱신."""
    db = SessionLocal()
    try:
        rel = db.get(LongformRelease, release_id)
        if not rel:
            return
        job = db.get(Job, rel.job_id)
        if not job:
            rel.status = "failed"; rel.error = "원본 곡 없음"; db.commit(); return

        rel.status = "rendering"
        db.commit()

        work = WORK_DIR / f"longform_{release_id}"
        work.mkdir(parents=True, exist_ok=True)

        # 1) 소스 곡들의 오디오 확보 (컴필레이션이면 여러 곡을 이어붙임)
        source_ids = rel.source_job_ids or [rel.job_id]

        async def _acquire(j: Job, dest: Path) -> Path | None:
            local = archiver.archived_path_for(j)
            if local and Path(local).exists():
                import shutil as _sh
                _sh.copyfile(local, dest)
                return dest
            url = archiver.extract_audio_url(j.output)
            if not url:
                return None
            await download_audio(url, dest)
            return dest

        track_paths: list[Path] = []
        for idx, jid in enumerate(source_ids):
            sj = db.get(Job, jid)
            if not sj:
                continue
            got = await _acquire(sj, work / f"track_{idx}.mp3")
            if got:
                track_paths.append(got)
        if not track_paths:
            rel.status = "failed"; rel.error = "오디오 소스 없음(보관/URL 모두)"; db.commit(); return

        # 여러 곡이면 크로스페이드로 이어붙여 하나의 시퀀스로
        audio_path = work / "sequence.mp3"
        await concat_audios(track_paths, audio_path)

        # 2) 커버 — 깔끔한 니치 기반 제목 (내부 곡명 대신)
        title_hint = {"sleep": "Deep Sleep Piano", "study": "Focus Piano",
                      "cinematic": "Ambient"}.get(rel.niche, "Deep Sleep")
        subtitle = {"sleep": "Relaxing Piano for Sleep", "study": "Focus & Study",
                    "cinematic": "Calm Ambient"}.get(rel.niche, "Relax")
        from .config import get_settings
        channel_name = get_settings().channel_name
        cover_path = work / "cover.png"
        # AI 장면 커버 우선(분위기 있는 썸네일), 실패 시 PIL 그라데이션 폴백
        ai = await make_ai_cover(db, cover_path, title=title_hint[:40], subtitle=subtitle,
                                 niche=rel.niche, watermark=channel_name)
        if not ai:
            make_ambient_cover(cover_path, title=title_hint[:40], subtitle=subtitle,
                               niche=rel.niche, seed=release_id, watermark=channel_name)
        rel.cover_path = str(cover_path)
        db.commit()

        # 3) 루프 인코딩
        video_path = work / "out.mp4"
        result = await encode_loop_video(cover_path, audio_path, video_path,
                                         target_sec=rel.target_sec)

        rel.video_path = result["video_path"]
        rel.duration_sec = result["duration_sec"]
        rel.file_size = result["file_size"]
        rel.status = "done"
        rel.error = ""
        db.add(AuditLog(actor="video_editor", action="longform.rendered",
                        target=f"release:{release_id}",
                        detail={"job_id": rel.job_id, "sec": rel.duration_sec,
                                "mb": rel.file_size // 1024 // 1024}))
        db.commit()
    except Exception as e:
        log.exception(f"longform render failed: {release_id}")
        try:
            rel = db.get(LongformRelease, release_id)
            if rel:
                rel.status = "failed"; rel.error = str(e)[:500]; db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()
