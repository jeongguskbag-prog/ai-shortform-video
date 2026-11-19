import json

import httpx
import pytest

from app.config import Settings
from app.services import audio, video

from .conftest import requires_ffmpeg

SETTINGS = Settings(max_retries=2, _env_file=None)


async def _probe_streams(path):
    out = await video.run_command(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)],
        timeout=30,
    )
    return {s["codec_type"]: s["codec_name"] for s in json.loads(out)["streams"]}


async def _make_clip(path, seconds):
    """나레이션이 든 영상 대신 쓰는 짧은 테스트 영상 (색 화면 + 사인파)."""
    await video.run_command(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=blue:s=180x320:d={seconds}:r=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        timeout=60,
    )


def test_music_queries_follow_tone_with_broad_fallback():
    assert audio.music_queries_for("energetic")[0] == "upbeat"
    assert audio.music_queries_for(" Calm ")[0] == "calm piano"
    assert audio.music_queries_for("따뜻하고 잔잔한") == audio.DEFAULT_MUSIC_QUERIES  # 직접 입력한 분위기
    # 결과가 0곡이던 검색어("corporate background")를 쓰지 않고, 모든 분위기에 넓은 예비 검색어가 있음
    for queries in audio.TONE_MUSIC_QUERIES.values():
        assert len(queries) >= 2 and "corporate background" not in queries


@pytest.mark.asyncio
async def test_openverse_music_falls_back_to_next_query(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.openverse.org":
            if request.url.params["q"] == "acoustic":
                return httpx.Response(200, json={"results": []})  # 첫 검색어는 결과 없음
            return httpx.Response(200, json={"results": [
                {"id": "p", "url": "https://cdn.example/p.mp3", "duration": 90_000, "title": "Keys", "creator": "Ann",
                 "license": "cc0"}]})
        return httpx.Response(200, content=b"ID3 fake mp3")

    source = audio.OpenverseMusicSource(SETTINGS, transport=httpx.MockTransport(handler))
    assert await source.fetch("informative", tmp_path / "m.mp3") == "Keys - Ann / Openverse (CC0)"
    assert [r.url.params["q"] for r in seen if r.url.host == "api.openverse.org"] == ["acoustic", "piano"]


def _openverse(payload, seen, broken=(), statuses=None):
    statuses = list(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.openverse.org":
            if statuses:
                return httpx.Response(statuses.pop(0), text="server error")
            return httpx.Response(200, json=payload)
        if str(request.url) in broken:
            return httpx.Response(404)
        return httpx.Response(200, content=b"ID3 fake mp3")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_openverse_music_skips_short_clips_and_broken_downloads(tmp_path):
    payload = {
        "results": [
            {"id": "sfx", "url": "https://cdn.example/sfx.mp3", "duration": 3_000, "title": "Click"},
            {"id": "gone", "url": "https://cdn.example/gone.mp3", "duration": 90_000, "title": "Gone"},
            {"id": "ok", "url": "https://cdn.example/ok.mp3", "duration": 120_000, "title": "Sunny",
             "creator": "Jane", "license": "by", "license_version": "3.0"},
        ]
    }
    seen = []
    source = audio.OpenverseMusicSource(SETTINGS, transport=_openverse(payload, seen, {"https://cdn.example/gone.mp3"}))

    credit = await source.fetch("energetic", tmp_path / "music.mp3")

    assert credit == "Sunny - Jane / Openverse (BY 3.0)"
    assert (tmp_path / "music.mp3").read_bytes() == b"ID3 fake mp3"
    search = seen[0].url.params
    assert (search["q"], search["category"], search["license"]) == ("upbeat", "music", "cc0,pdm,by")
    assert "https://cdn.example/sfx.mp3" not in [str(r.url) for r in seen]  # 30초 미만은 받지 않음


@pytest.mark.asyncio
async def test_openverse_music_retries_server_errors_and_reports_no_results(tmp_path):
    seen = []
    track = {"id": "t", "url": "https://cdn.example/t.mp3", "duration": 60_000, "title": "T"}
    source = audio.OpenverseMusicSource(SETTINGS, transport=_openverse({"results": [track]}, seen, statuses=[500]))
    assert (await source.fetch("calm", tmp_path / "m.mp3")).startswith("T - Unknown")  # 500 한 번 뒤 성공

    empty = audio.OpenverseMusicSource(SETTINGS, transport=_openverse({"results": []}, []))
    with pytest.raises(audio.MusicNotFoundError, match="calm piano / piano"):
        await empty.fetch("calm", tmp_path / "m2.mp3")


@requires_ffmpeg
@pytest.mark.asyncio
async def test_mix_soundtrack_loops_music_adds_sfx_and_keeps_video(tmp_path):
    clip, music, sfx, out = (tmp_path / n for n in ("clip.mp4", "music.wav", "sfx.wav", "out.mp4"))
    await _make_clip(clip, 4)
    await video.run_command(  # 영상보다 짧은 곡 → 반복 재생되어야 함
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=220:duration=1.5", str(music)],
        timeout=30,
    )
    await audio.make_whoosh(sfx)
    assert 0.4 < await video.probe_duration(sfx) < 0.5

    await video.mix_soundtrack(
        clip, out, duration=4.0, music_path=music, sfx_path=sfx, sfx_times=[1.0, 2.5, 9.0], timeout=60
    )

    assert abs(await video.probe_duration(out) - 4.0) < 0.15
    assert await _probe_streams(out) == {"video": "h264", "audio": "aac"}


@requires_ffmpeg
@pytest.mark.asyncio
async def test_mix_soundtrack_works_with_only_music_or_only_sfx(tmp_path):
    clip, sfx = tmp_path / "clip.mp4", tmp_path / "sfx.wav"
    await _make_clip(clip, 2)
    await audio.make_whoosh(sfx)

    await video.mix_soundtrack(clip, tmp_path / "sfx_only.mp4", duration=2.0, sfx_path=sfx, sfx_times=[1.0], timeout=60)
    await video.mix_soundtrack(clip, tmp_path / "music_only.mp4", duration=2.0, music_path=sfx, timeout=60)
    for name in ("sfx_only.mp4", "music_only.mp4"):
        assert await _probe_streams(tmp_path / name) == {"video": "h264", "audio": "aac"}

    with pytest.raises(ValueError):
        await video.mix_soundtrack(clip, tmp_path / "none.mp4", duration=2.0, timeout=60)
