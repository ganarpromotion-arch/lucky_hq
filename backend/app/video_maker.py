"""
영상 편집자 직원 (video_editor)

곡 1개 → mp4 1편

흐름:
  1. Mureka audio_url 다운로드 (mp3 또는 wav)
  2. 무드/제목 기반 정지 이미지 생성 (1920x1080 가로 16:9)
  3. ffmpeg로 이미지 + 오디오 → mp4 인코딩

스타일:
  - 무드별 대각선 그라데이션 + 부드러운 발광점 (radial bloom)
  - 한국어 제목은 레포 내부 폰트 (Noto Sans KR Bold)로 안정적으로 렌더
"""
from __future__ import annotations
import asyncio
import logging
import math
import os
import random
import shutil
import subprocess
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("lucky_hq.video")

# 작업 디렉토리 (Railway 컨테이너에서 임시)
WORK_DIR = Path(os.environ.get("LUCKY_HQ_WORK_DIR", "/tmp/lucky_hq_videos"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# 레포 내부 폰트 — 우선순위 1
APP_DIR = Path(__file__).resolve().parent
BUNDLED_FONT = APP_DIR / "assets" / "fonts" / "NotoSansKR-Bold.otf"

# 가로 16:9 해상도 (유튜브/X 일반)
W, H = 1920, 1080

# 무드별 색상 팔레트 — 더 풍부한 3색 그라데이션 (위 / 중간 / 아래)
MOOD_PALETTES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "warm":      ((255, 200, 130), (220, 130, 90),  (140, 70, 60)),    # 노을 — 황금 → 주황 → 진갈
    "bright":    ((180, 220, 255), (255, 220, 130), (255, 150, 90)),   # 한낮 — 하늘 → 노랑 → 산호
    "cold":      ((220, 230, 245), (140, 175, 215), (50, 80, 130)),    # 겨울 — 흰 → 청 → 진청
    "fresh":     ((220, 240, 200), (140, 210, 170), (60, 130, 110)),   # 봄 — 연두 → 민트 → 숲
    "emotional": ((230, 200, 240), (170, 130, 200), (90, 60, 130)),    # 감성 — 라일락 → 보라 → 진보라
    "energetic": ((255, 220, 110), (240, 100, 90),  (180, 50, 110)),   # 에너지 — 노랑 → 주황 → 핑크
    "cozy":      ((240, 210, 170), (200, 140, 100), (120, 70, 50)),    # 아늑 — 베이지 → 카멜 → 갈
    "calm":      ((230, 235, 240), (180, 195, 220), (100, 120, 160)),  # 차분 — 흰회 → 회청 → 진회청
    "dreamy":    ((180, 180, 230), (110, 100, 180), (40, 30, 80)),     # 꿈 — 라벤더 → 보라 → 야경
    "playful":   ((255, 220, 180), (255, 130, 150), (190, 70, 130)),   # 발랄 — 살구 → 핑크 → 마젠타
    "modern":    ((220, 230, 240), (130, 150, 190), (60, 80, 120)),    # 모던 — 회청 → 청 → 진청
}
DEFAULT_PALETTE = MOOD_PALETTES["modern"]


def _find_font_path() -> str:
    """폰트 우선순위:
    1. 레포 번들 (NotoSansKR-Bold.otf) — 항상 작동
    2. 시스템 NotoSansCJK
    3. fc-match로 한글 폰트 검색
    """
    # 1) 번들
    if BUNDLED_FONT.exists():
        return str(BUNDLED_FONT)

    # 2) 시스템 후보
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    import glob
    for pat in candidates:
        if "*" in pat:
            matches = glob.glob(pat)
            if matches:
                return matches[0]
        elif Path(pat).exists():
            return pat
    # nixpacks 경로 (Railway)
    for pat in [
        "/nix/store/*/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/nix/store/*/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        matches = glob.glob(pat)
        if matches:
            return matches[0]
    return ""


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path = _find_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception as e:
            log.warning(f"폰트 로드 실패 ({path}): {e}")
    log.warning("한국어 폰트를 찾지 못함 — 기본 폰트 폴백 (한글 깨짐 가능)")
    return ImageFont.load_default()


def _three_stop_gradient(mood: str, angle_deg: float = 100.0) -> Image.Image:
    """3-stop 대각선 그라데이션. 위→중간→아래 방향에 약간 기울임."""
    top, mid, bot = MOOD_PALETTES.get(mood, DEFAULT_PALETTE)
    img = Image.new("RGB", (W, H))
    px = img.load()

    # 대각선 단위 벡터 (수직에서 angle만큼 회전)
    rad = math.radians(angle_deg)
    dx, dy = math.sin(rad), math.cos(rad)  # angle=0 → 위→아래

    # 그라데이션을 따라 위치(0~1)를 계산할 때 쓰는 정규화 길이
    # 화면 모서리 두 점의 투영값 차이로 정규화
    proj_min = min(
        x * dx + y * dy for x in (0, W - 1) for y in (0, H - 1)
    )
    proj_max = max(
        x * dx + y * dy for x in (0, W - 1) for y in (0, H - 1)
    )
    span = max(1.0, proj_max - proj_min)

    def lerp(a, b, t):
        return int(a + (b - a) * t)

    for y in range(H):
        for x in range(W):
            t = ((x * dx + y * dy) - proj_min) / span  # 0~1
            if t < 0.5:
                # top → mid
                u = t * 2
                r = lerp(top[0], mid[0], u)
                g = lerp(top[1], mid[1], u)
                b = lerp(top[2], mid[2], u)
            else:
                # mid → bot
                u = (t - 0.5) * 2
                r = lerp(mid[0], bot[0], u)
                g = lerp(mid[1], bot[1], u)
                b = lerp(mid[2], bot[2], u)
            px[x, y] = (r, g, b)
    return img


def _add_radial_bloom(img: Image.Image, mood: str, seed: int = 0) -> Image.Image:
    """무드 색조와 어울리는 부드러운 빛 발광점 1~2개 추가."""
    rng = random.Random(seed)
    top, _, _ = MOOD_PALETTES.get(mood, DEFAULT_PALETTE)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    for _ in range(rng.randint(1, 2)):
        cx = rng.randint(W // 4, 3 * W // 4)
        cy = rng.randint(H // 4, H // 2)  # 위쪽에 발광
        radius = rng.randint(W // 3, W // 2)

        bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(bloom)
        # 가운데 밝은 점 (top 색)
        d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                  fill=(top[0], top[1], top[2], 90))
        bloom = bloom.filter(ImageFilter.GaussianBlur(radius=radius // 2))
        overlay = Image.alpha_composite(overlay, bloom)

    base = img.convert("RGBA")
    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out


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
                   subtitle: str = "", watermark: str = "Lucky HQ",
                   seed: int | None = None) -> Path:
    """정지 이미지 1장 생성 → out_path 저장.

    매번 약간씩 다른 그라데이션 각도 + 발광 위치 (seed로 결정).
    """
    s = seed if seed is not None else random.randint(0, 10000)
    rng = random.Random(s)

    # 1) 그라데이션 각도 약간 변동 (90~115도)
    angle = 90 + rng.uniform(0, 25)
    img = _three_stop_gradient(mood, angle_deg=angle)

    # 2) 부드러운 발광 추가
    img = _add_radial_bloom(img, mood, seed=s)

    draw = ImageDraw.Draw(img)

    # 3) 가운데 어두운 박스 (텍스트 가독성)
    box_h = 520
    box_top = (H - box_h) // 2 + 30
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([(120, box_top), (W - 120, box_top + box_h)],
                 fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 4) 제목 (큰 폰트)
    title_font = _load_font(120)
    margin = 200
    max_title_width = W - margin * 2
    title_lines = _wrap_text(title or "Lucky HQ", draw, title_font, max_title_width)[:2]

    line_h = 144
    total_h = len(title_lines) * line_h
    start_y = box_top + (box_h - total_h) // 2 - 40
    for i, line in enumerate(title_lines):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        line_w = bbox[2] - bbox[0]
        x = (W - line_w) // 2
        y = start_y + i * line_h
        # 그림자
        draw.text((x + 4, y + 4), line, font=title_font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255))

    # 5) 서브타이틀
    if subtitle:
        sub_font = _load_font(50)
        sub_lines = _wrap_text(subtitle, draw, sub_font, max_title_width)[:2]
        sub_y = start_y + total_h + 50
        for i, line in enumerate(sub_lines):
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            line_w = bbox[2] - bbox[0]
            x = (W - line_w) // 2
            draw.text((x + 2, sub_y + i * 60 + 2), line, font=sub_font,
                      fill=(0, 0, 0, 180))
            draw.text((x, sub_y + i * 60), line, font=sub_font,
                      fill=(255, 255, 255, 220))

    # 6) 워터마크 (상단)
    wm_font = _load_font(44)
    bbox = draw.textbbox((0, 0), watermark, font=wm_font)
    wm_w = bbox[2] - bbox[0]
    draw.text(((W - wm_w) // 2, 60), watermark, font=wm_font,
              fill=(255, 255, 255, 230))

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
                             mood: str = "modern", subtitle: str = "",
                             seed: int | None = None,
                             preset_image_path: str | None = None) -> dict:
    """곡 1개에 대한 mp4 생성 — 다운로드 + 이미지 + 인코딩.

    preset_image_path가 주어지면 그 이미지를 그대로 사용 (이미지 시안 선택).
    seed만 있으면 PIL로 동일 이미지 재현.

    Returns: {ok, video_path, image_path, duration_sec, file_size, error}
    """
    work = WORK_DIR / f"job_{job_id}"
    work.mkdir(parents=True, exist_ok=True)

    audio_path = work / "audio.mp3"
    video_path = work / "out.mp4"

    try:
        # 1. 표지 이미지 결정
        if preset_image_path:
            preset = Path(preset_image_path)
            if not preset.exists():
                raise RuntimeError(f"preset 이미지 없음: {preset_image_path}")
            image_path = preset
        else:
            image_path = work / "thumb.png"
            make_thumbnail(image_path, title=title, mood=mood, subtitle=subtitle, seed=seed)

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
