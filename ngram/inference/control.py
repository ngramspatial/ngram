"""Per-Entity inference pause, shared by chat, embeddings, and background work."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any


class InferencePausedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Inference is paused. Resume inference to continue.")


class InferenceControl:
    def __init__(self, marker: Path) -> None:
        self.marker = marker
        self._requests: set[asyncio.Task] = set()
        self._generation = 0

    @property
    def paused(self) -> bool:
        return self.marker.exists()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            self.marker.touch(exist_ok=True)
            self._generation += 1
            for task in tuple(self._requests):
                task.cancel()
        else:
            self.marker.unlink(missing_ok=True)

    def require_running(self) -> None:
        if self.paused:
            raise InferencePausedError()

    async def run(self, call: Callable[[], Awaitable[Any]]) -> Any:
        self.require_running()
        generation = self._generation
        # Own the request task, never the daemon, platform listener, or scheduler.
        task = asyncio.ensure_future(call())
        self._requests.add(task)
        try:
            result = await task
            self.require_running()
            if generation != self._generation:
                raise InferencePausedError()
            return result
        except asyncio.CancelledError:
            if self.paused or generation != self._generation:
                raise InferencePausedError() from None
            raise
        finally:
            self._requests.discard(task)


class ControlledInferenceProvider:
    def __init__(self, provider: Any, control: InferenceControl) -> None:
        self.provider = provider
        self.control = control

    def __getattr__(self, name: str) -> Any:
        # Non-generating provider operations (catalog, health, close) still work.
        return getattr(self.provider, name)

    async def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        result = await self.control.run(lambda: self.provider.chat_completion(*args, **kwargs))
        if hasattr(result, "__aiter__"):
            return self._stream(result)
        return result

    async def _stream(self, source: AsyncIterator[str]) -> AsyncIterator[str]:
        iterator = source.__aiter__()
        try:
            while True:
                try:
                    chunk = await self.control.run(lambda: anext(iterator))
                except StopAsyncIteration:
                    break
                yield chunk
        finally:
            close = getattr(iterator, "aclose", None)
            if close:
                await close()

    async def embed(self, *args: Any, **kwargs: Any) -> list[float]:
        return await self.control.run(lambda: self.provider.embed(*args, **kwargs))
