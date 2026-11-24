"""릴스 영상 만들기: (이미지) + TTS 나레이션 + 자막 → 세로 1080×1920 MP4.

필요: 시스템에 ffmpeg / ffprobe 설치 (sudo apt install ffmpeg),  pip 패키지 gTTS
단독 실행(테스트):
    python -m reels.make_video "안녕하세요. 라즈베리파이로 만든 첫 번째 릴스입니다." --out data/test.mp4
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from common.config import data_dir, env, env_int

WIDTH, HEIGHT = 1080, 1920
TAIL_SECONDS = 0.7  # 나레이션이 끝난 뒤 잠깐 더 보여 주는 시간


class VideoError(RuntimeError):
    pass


# ── 텍스트/자막 (순수 함수: ffmpeg 없이 테스트 가능) ─────────────────────────
def narration_from_text(text: str) -> str:
    """게시 문구에서 URL/해시태그를 빼고 읽을 문장만 남긴다."""
    text = re.sub(r"https?://\S+", "", text or "")
    text = re.sub(r"#\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def split_chunks(text: str, max_chars: int = 16) -> list[str]:
    """자막 한 장면에 들어갈 길이로 나눈다(공백 기준, 문장부호에서 우선 끊음)."""
    words = text.split()
    chunks: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
        if cur and cur[-1] in ".?!。…" and len(cur) >= max_chars // 2:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(chunks: list[str], duration: float) -> str:
    """글자 수에 비례해 전체 길이를 나눠 자막 타이밍을 만든다."""
    total = sum(len(c) for c in chunks) or 1
    out, t = [], 0.0
    for i, c in enumerate(chunks, 1):
        d = duration * len(c) / total
        out.append(f"{i}\n{srt_time(t)} --> {srt_time(t + d)}\n{c}\n")
        t += d
    return "\n".join(out)


# ── 외부 도구 호출 ───────────────────────────────────────────────────────────
def _need(tool: str) -> None:
    if shutil.which(tool) is None:
        raise VideoError(f"{tool} 이(가) 설치돼 있지 않습니다. (sudo apt install ffmpeg)")


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise VideoError(f"{cmd[0]} 실패:\n{proc.stderr[-1500:]}")


def synth_tts(text: str, out_mp3: Path, lang: str = "ko") -> None:
    from gtts import gTTS  # 지연 import: 영상 제작 때만 필요

    gTTS(text=text, lang=lang).save(str(out_mp3))


def audio_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise VideoError(f"오디오 길이를 읽지 못했습니다: {proc.stderr[-300:]}")


def download_image(url: str, out: Path) -> Path:
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


# ── 영상 조립 ────────────────────────────────────────────────────────────────
def build_video(
    text: str,
    out_path: Path,
    *,
    images: list[Path] | None = None,
    work_dir: Path | None = None,
    lang: str | None = None,
    font: str | None = None,
    bg_color: str | None = None,
) -> Path:
    """text 를 읽어 주는 세로 영상을 out_path 에 만든다. images 가 없으면 단색 배경."""
    _need("ffmpeg")
    _need("ffprobe")
    narration = narration_from_text(text)
    if not narration:
        raise VideoError("나레이션으로 읽을 텍스트가 비어 있습니다.")

    lang = lang or env("REEL_LANG", "ko") or "ko"
    font = font or env("REEL_FONT", "Noto Sans CJK KR") or "Noto Sans CJK KR"
    bg_color = bg_color or env("REEL_BG_COLOR", "0x1b2a41") or "0x1b2a41"

    work = Path(work_dir or (data_dir() / "reel_work"))
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) 나레이션 + 자막
    mp3 = work / "narration.mp3"
    synth_tts(narration, mp3, lang)
    duration = audio_duration(mp3) + TAIL_SECONDS
    # 자막 글자 크기(1080x1920 화면 기준 픽셀). 클수록 한 줄에 들어가는 글자 수가 줄어 자막 조각도 짧아진다.
    font_px = env_int("REEL_FONT_SIZE", 96)
    max_chars = max(6, int((WIDTH - 160) / font_px))
    (work / "subs.srt").write_text(
        build_srt(split_chunks(narration, max_chars), duration - TAIL_SECONDS), encoding="utf-8")

    # 2) 장면 클립(이미지마다 균등 분할, 이미지가 없으면 단색 1장)
    sources = images or [None]
    seg = duration / len(sources)
    scale = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={bg_color},setsar=1,format=yuv420p"
    )
    clip_names = []
    for i, img in enumerate(sources):
        name = f"clip_{i}.mp4"
        if img is None:
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={bg_color}:s={WIDTH}x{HEIGHT}:r=30",
                   "-t", f"{seg:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", name]
        else:
            cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{seg:.3f}", "-i", str(Path(img).resolve()),
                   "-vf", scale, "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-an", name]
        _run(cmd, cwd=work)
        clip_names.append(name)

    (work / "list.txt").write_text("".join(f"file '{n}'\n" for n in clip_names), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "video_only.mp4"], cwd=work)

    # 3) 오디오 + 자막 합성 (자막 파일은 work 폴더 기준 상대경로로 지정해 OS 별 경로 문제를 피함)
    # SRT 자막의 style 숫자(글자 크기·여백·테두리)는 "세로 288 짜리 가상 화면" 기준 단위입니다(ffmpeg 기본값).
    # 1080x1920 화면의 픽셀로 생각한 값을 그대로 넣으면 글자가 화면 밖으로 밀려나 자막이 안 보이므로 환산해서 넣습니다.
    k = 288 / HEIGHT
    style = (f"FontName={font},FontSize={round(font_px * k)},Alignment=2,MarginV={round(380 * k)},"
             f"Outline={max(1, round(4 * k))},Shadow=0,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&")
    vf = f"subtitles=subs.srt:force_style='{style}'"
    _run(["ffmpeg", "-y", "-i", "video_only.mp4", "-i", "narration.mp3", "-vf", vf,
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
          "-shortest", "-movflags", "+faststart", str(out_path.resolve())], cwd=work)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="릴스 영상 1건 만들기(테스트용)")
    ap.add_argument("text", help="읽어 줄 문장")
    ap.add_argument("--image", action="append", default=[], help="배경 이미지 파일(여러 번 지정 가능)")
    ap.add_argument("--out", default=str(data_dir() / "reel_test.mp4"))
    args = ap.parse_args(argv)
    try:
        path = build_video(args.text, Path(args.out), images=[Path(p) for p in args.image] or None)
    except VideoError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 1
    print(f"만들었습니다: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
