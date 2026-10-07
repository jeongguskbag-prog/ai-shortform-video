import json
import subprocess

import pytest
from PIL import ImageFont

from app.services import video

from .conftest import FakeTTS, requires_ffmpeg


def test_wrap_text_respects_width(spec):
    font = ImageFont.truetype(str(spec.font_path), 40)
    text = "아주아주아주아주아주아주아주아주긴단어 와 짧은 단어들이 섞인 자막"
    lines = video.wrap_text(text, font, 200)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 200 for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_render_and_concat(tmp_path, spec):
    from PIL import Image

    img = tmp_path / "img.png"
    Image.new("RGB", (500, 500), (10, 120, 200)).save(img)
    audio = tmp_path / "a.mp3"
    await FakeTTS().synthesize("x", audio)
    sub = tmp_path / "sub.png"
    await video.render_subtitle_png("따옴표 ' 콜론 : 백슬래시 \\ 테스트", spec, sub)

    scenes = []
    for i in range(2):
        out = tmp_path / f"it's scene {i}.mp4"  # 경로의 따옴표/공백 이스케이프 확인
        dur = await video.render_scene(
            image_path=img, audio_path=audio, subtitle_png=sub, output_path=out,
            spec=spec, zoom_in=i == 0,
        )
        assert dur == pytest.approx(1.0 + spec.padding_sec, abs=0.1)
        scenes.append(out)

    final = tmp_path / "final.mp4"
    total = await video.concat_scenes(scenes, final, timeout=60)
    assert total == pytest.approx(2 * (1.0 + spec.padding_sec), abs=0.25)

    info = _probe(final)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (spec.width, spec.height)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


@requires_ffmpeg
@pytest.mark.asyncio
async def test_run_command_reports_stderr(tmp_path):
    with pytest.raises(video.FFmpegError, match="exit"):
        await video.run_command(["ffprobe", str(tmp_path / "missing.mp4")], timeout=10)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_run_command_falls_back_without_async_subprocess(monkeypatch, tmp_path):
    """Windows SelectorEventLoop처럼 비동기 subprocess가 없는 환경에서도 동작해야 함."""
    import asyncio

    async def unsupported(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unsupported)
    audio = tmp_path / "a.mp3"
    await FakeTTS().synthesize("x", audio)
    assert await video.probe_duration(audio) == pytest.approx(1.0, abs=0.1)
    with pytest.raises(video.FFmpegError, match="exit"):
        await video.run_command(["ffprobe", str(tmp_path / "missing.mp4")], timeout=10)
