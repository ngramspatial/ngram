"""Abstract platform interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class Platform(ABC):
    @abstractmethod
    async def connect(self) -> None:
        ...

    @abstractmethod
    async def send_message(self, channel: str, content: str) -> None:
        ...

    @abstractmethod
    async def send_tool_activity(self, description: str) -> None:
        """Harness-only status line before an external tool runs (web, file, MCP)."""
        ...

    async def send_tool_result(
        self,
        tool_name: str,
        output: str,
        *,
        error: bool = False,
    ) -> None:
        """Optional harness telemetry after a tool finishes.

        Platforms opt in when they have a safe surface for tool output. The
        default remains a no-op so messaging integrations do not change.
        """
        return None

    async def send_spatial_action(self, action: dict[str, Any]) -> bool:
        """Deliver a spatial action immediately when the surface supports it.

        Return ``True`` when accepted. Non-spatial platforms keep the default
        so callers can fall back to reliable end-of-turn buffering.
        """
        return False

    @abstractmethod
    async def on_message(self, callback: Callable[..., Any]) -> None:
        ...

    @abstractmethod
    async def set_presence(self, status: str) -> None:
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @abstractmethod
    def get_person_id(self, message: Any) -> str:
        ...

    @abstractmethod
    def get_person_name(self, message: Any) -> str:
        ...

    async def send_attachment_bytes(
        self,
        channel: str,
        data: bytes,
        *,
        content_type: str | None = None,
        filename: str = "attachment.bin",
    ) -> None:
        """Send binary outbound (e.g. bytes loaded from ``Entity.read_stored_attachment``). CLI: no-op."""
        return None

    async def send_audio(self, channel: str, path: str) -> bool:
        """Send an audio/voice file from disk. Return ``True`` on successful delivery."""
        return False

    async def send_image(self, channel: str, path: str) -> None:
        """Send an image file from disk (platform-specific best effort)."""
        return None

    def get_active_remote_session(self, channel: str) -> dict[str, Any] | None:
        """Return session metadata for a live remote-control context in this channel, if any."""
        return None

    async def set_active_remote_session(
        self,
        channel: str,
        session: dict[str, Any] | None,
    ) -> None:
        """Store or clear active remote-session metadata for the channel."""
        return None

    async def upsert_remote_session_card(
        self,
        channel: str,
        *,
        image_bytes: bytes | None,
        caption: str,
        session: dict[str, Any] | None = None,
    ) -> None:
        """Create or update a live remote-session card (for example, one Telegram photo message)."""
        return None

    async def clear_remote_session_card(self, channel: str) -> None:
        """Forget any live remote-session card bookkeeping for the channel."""
        return None

    async def fetch_recent_messages(
        self,
        channel: str,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """Retrieve recent messages from a channel for the observe tool.

        Returns list of dicts with keys: sender, content, timestamp (ISO str).
        Default implementation returns empty — platforms override as able.
        """
        return []
