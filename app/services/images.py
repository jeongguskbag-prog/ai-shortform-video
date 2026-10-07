"""씬 이미지 생성 (Imagen) 및 실패 시 대체 이미지."""

import asyncio
import colorsys
import hashlib
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image, ImageDraw

from ..config import Settings
from ..retry import with_retry

STYLE_SUFFIX = (
    "vertical 9:16 composition, cinematic lighting, highly detailed, sharp focus, "
    "no text, no captions, no watermark"
)


class ImageGenerationError(RuntimeError):
    pass


class ImagenGenerator:
    def __init__(self, client: genai.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def generate(self, prompt: str, output_path: Path) -> None:
        config = types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="9:16",
            negative_prompt="text, letters, watermark, logo, blurry, distorted",
        )

        async def call() -> None:
            result = await self._client.aio.models.generate_images(
                model=self._settings.image_model,
                prompt=f"{prompt}, {STYLE_SUFFIX}",
                config=config,
            )
            images = result.generated_images or []
            image_bytes = images[0].image.image_bytes if images and images[0].image else None
            if not image_bytes:
                # 안전 필터에 걸리면 이미지 없이 응답이 옵니다.
                raise ImageGenerationError("이미지가 생성되지 않았습니다 (안전 필터 차단 가능).")
            output_path.write_bytes(image_bytes)

        await with_retry(call, attempts=self._settings.max_retries, what="이미지 생성")


def _make_placeholder(seed_text: str, size: tuple[int, int], output_path: Path) -> None:
    """프롬프트별로 색이 다른 세로 그라디언트 이미지를 만듭니다."""
    width, height = size
    hue = int(hashlib.sha256(seed_text.encode()).hexdigest()[:4], 16) / 0xFFFF
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.55, 0.45))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((hue + 0.12) % 1, 0.7, 0.12))

    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        draw.line([(0, y), (width, y)], fill=color)
    img.save(output_path, format="PNG")


async def make_placeholder(seed_text: str, size: tuple[int, int], output_path: Path) -> None:
    await asyncio.to_thread(_make_placeholder, seed_text, size, output_path)
