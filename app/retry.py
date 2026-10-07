import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    what: str,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> T:
    """지수 백오프(+지터)로 비동기 호출을 재시도합니다."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random())
            logger.warning("%s 실패 (%d/%d): %s — %.1f초 후 재시도", what, attempt, attempts, exc, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
