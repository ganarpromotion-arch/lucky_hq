"""
영상 편집자 직원 (video_editor)

곡 1개 → mp4 1편

흐름:
  1. Mureka audio_url 다운로드 (mp3 또는 wav)
  2. 무드/제목 기반 정지 이미지 생성 (1080x1920 쇼츠 비율)
  3. ffmpeg로 이미지 + 오디오 → mp4 인코딩
  4. mp4 파일 경로 반환

스타일:
  - 무드별 그라데이션 배경 (warm/cold/dreamy 등)
  - 한국어 제목 + Lucky HQ 워터마크
"""
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("lucky_hq.video")

# 작업 디렉토리 (Railway 컨테이너에서 임시)
WORK_DIR = Path(os.environ.get("LUCKY_HQ_WORK_DIR", "/tmp/lucky_hq_videos"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# 쇼츠 해상도
W, H = 1080, 1920

# 무드별 색상 팔레트 (배경 그라데이션 시작/끝)
MOOD_COLORS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "warm":      ((255, 184, 105), (217, 119, 87)),   # 호박/연주황
    "bright":    ((135, 206, 250), (255, 218, 121)),  # 하늘/밝은 노랑
    "cold":      ((180, 200, 230), (90, 130, 180)),   # 차가운 푸른
    "fresh":     ((180, 230, 180), (110, 200, 180)),  # 봄 연두/민트
    "emotional": ((200, 170, 230), (130, 100, 180)),  # 보라
    "energetic": ((255, 140, 90), (220, 60, 110)),    # 강한 주황/분홍
    "cozy":      ((220, 180, 130), (160, 100, 70)),   # 따뜻한 갈색
    "calm":      ((220, 230, 240), (170, 180, 210)),  # 부드러운 회청
    "dreamy":    ((130, 140, 200), (60, 50, 110)),    # 어두운 보라
    "playful":   ((255, 200, 130), (250, 110, 130)),  # 발랄한 살구/핑크
    "modern":    ((180, 200, 220), (90, 110, 140)),   # 차분한 회청
}
DEFAULT_PALETTE = MOOD_COLORS["modern"]


def _find_korean_font() -> str | None:
    """시스템에서 한국어 폰트 찾기. nixpacks의 noto-fonts-cjk-sans 우선."""
    candidates = [
        "/nix/store/*/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",  # nixpacks
        "/nix/store/*/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
    ]
    import glob
    for pat in candidates:
        if "*" in pat:
            matches = glob.glob(pat)
            if matches:
                return matches[0]
        elif Path(pat).exists():
            return pat
    return None


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path = _find_korean_font()
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    # 폴백: 기본 폰트 (한국어 깨질 수 있음)
    return ImageFont.load_default()


def _gradient_bg(mood: str) -> Image.Image:
    """무드별 그라데이션 배경. 위→아래 방향."""
    top, bottom = MOOD_COLORS.get(mood, DEFAULT_PALETTE)
    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(top[0] * (1 - ratio) + bottom[0] * ratio)
        g = int(top[1] * (1 - ratio) + bottom[1] * ratio)
        b = int(top[2] * (1 - ratio) + bottom[2] * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img


def _wrap_text(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont,
               max_width: int) -> list[str]:
    """글자 단위 줄바꿈 (한국어는 단어 분리가 약하므로 글자 단위)."""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines


def make_thumbnail(out_path: Path, title: str, mood: str = "modern",
                   subtitle: str = "", watermark: str = "Lucky HQ") -> Path:
    """정지 이미지 1장 생성 → out_path 저장."""
    img = _gradient_bg(mood)
    draw = ImageDraw.Draw(img)

    # 반투명 어두운 박스 (가운데 텍스트 가독성)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle([(60, H // 2 - 380), (W - 60, H // 2 + 380)], fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 제목 (큰 폰트, 가운데)
    title_font = _load_font(110)
    margin = 120
    max_title_width = W - margin * 2
    title_lines = _wrap_text(title or "Lucky HQ", draw, title_font, max_title_width)
    # 최대 3줄까지
    title_lines = title_lines[:3]

    line_h = 130
    total_h = len(title_lines) * line_h
    start_y = (H - total_h) // 2 - 60
    for i, line in enumerate(title_lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        x = (W - line_w) // 2
        y = start_y + i * line_h
        # 그림자
        draw.text((x + 3, y + 3), line, font=title_font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255))

    # 서브타이틀 (작게, 제목 아래)
    if subtitle:
        sub_font = _load_font(48)
        sub_lines = _wrap_text(subtitle, draw, sub_font, max_title_width)[:2]
        sub_y = start_y + total_h + 50
        for i, line in enumerate(sub_lines):
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            line_w = bbox[2] - bbox[0]
            x = (W - line_w) // 2
            draw.text((x, sub_y + i * 60), line, font=sub_font,
                      fill=(255, 255, 255, 200))

    # 워터마크 (상단)
    wm_font = _load_font(42)
    bbox = draw.textbbox((0, 0), watermark, font=wm_font)
    wm_w = bbox[2] - bbox[0]
    draw.text(((W - wm_w) // 2, 80), watermark, font=wm_font, fill=(255, 255, 255, 220))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


async def download_audio(url: str, out_path: Path, timeout: float = 120.0) -> Path:
    """Mureka audio_url 다운로드."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                    f.write(chunk)
    return out_path


def _ffmpeg_path() -> str:
    """ffmpeg 실행 경로 찾기."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    # nixpacks 경로
    import glob
    for pat in ["/nix/store/*/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        m = glob.glob(pat)
        if m:
            return m[0]
    raise RuntimeError("ffmpeg not found in PATH")


async def encode_video(image_path: Path, audio_path: Path, out_path: Path) -> dict:
    """정지 이미지 + 오디오 → mp4. ffmpeg 단일 호출.

    설정:
      - libx264, yuv420p (광범위 호환)
      - aac 오디오, 192k
      - 이미지는 -loop 1로 오디오 길이만큼 반복
      - -shortest로 오디오 끝나면 종료
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg_path()

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", "24",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패 (code {proc.returncode}): {stderr.decode('utf-8', 'ignore')[:500]}")

    # 영상 길이 추출 (ffprobe)
    duration = await _probe_duration(out_path)
    return {
        "video_path": str(out_path),
        "duration_sec": int(duration),
        "file_size": out_path.stat().st_size if out_path.exists() else 0,
    }


async def _probe_duration(path: Path) -> float:
    """ffprobe로 영상 길이(초) 가져오기."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip() or 0.0)
    except Exception:
        return 0.0


async def make_video_for_job(job_id: int, audio_url: str, title: str,
                             mood: str = "modern", subtitle: str = "") -> dict:
    """곡 1개에 대한 mp4 생성 — 다운로드 + 이미지 + 인코딩.

    Returns: {ok, video_path, image_path, duration_sec, file_size, error}
    """
    work = WORK_DIR / f"job_{job_id}"
    work.mkdir(parents=True, exist_ok=True)

    image_path = work / "thumb.png"
    audio_path = work / "audio.mp3"
    video_path = work / "out.mp4"

    try:
        # 1. 정지 이미지
        make_thumbnail(image_path, title=title, mood=mood, subtitle=subtitle)

        # 2. 오디오 다운로드
        await download_audio(audio_url, audio_path)

        # 3. mp4 인코딩
        result = await encode_video(image_path, audio_path, video_path)

        return {
            "ok": True,
            "image_path": str(image_path),
            "video_path": result["video_path"],
            "duration_sec": result["duration_sec"],
            "file_size": result["file_size"],
            "error": "",
        }
    except Exception as e:
        log.exception(f"video render failed for job {job_id}")
        return {
            "ok": False,
            "image_path": str(image_path) if image_path.exists() else "",
            "video_path": "",
            "duration_sec": 0,
            "file_size": 0,
            "error": str(e)[:500],
        }
