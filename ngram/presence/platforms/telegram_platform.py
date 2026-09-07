"""Telegram adapter: conversational relay + rich slash command UX."""

from __future__ import annotations

import asyncio
import base64
import html
import io
import os
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from telegram import BotCommand, BotCommandScopeChat, InputMediaPhoto, MenuButtonCommands, Update
from telegram.constants import ChatAction
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.helpers import escape_markdown

from ngram.models import Input
from ngram.presence.platforms.base import Platform
from ngram.presence.platforms.telegram_format import (
    build_status_html,
    command_menu_items,
    format_access_denied,
    format_body_md_reply_html_chunks,
    format_busy_status_html,
    format_commands_page,
    format_feelings_html,
    format_help_html,
    format_journal_html_chunks,
    format_me_html,
    format_memories_html,
    format_models_html,
    format_ping_html,
    format_privacy_allow_deny_usage_html,
    format_privacy_cannot_deny_last_html,
    format_privacy_help_html,
    format_privacy_invalid_id_html,
    format_privacy_locked_html,
    format_privacy_no_operators_html,
    format_privacy_opened_html,
    format_privacy_operator_required_html,
    format_privacy_status_html,
    format_private_usage_html,
    format_relationship_html,
    format_remote_session_caption,
    format_reset_html,
    format_routines_html,
    format_session_disabled_html,
    format_session_operator_required_html,
    format_session_status_html,
    format_start_html,
    format_tools_html,
    format_unknown_command,
    format_update_blocked_html,
    format_update_result_html,
    format_version_html,
    format_wakequiet_status_html,
    format_whoami_html,
    split_telegram_chunks,
)
from ngram.presence.platforms.telegram_privacy import (
    KEY_WAKE_QUIET,
    entity_state_delete,
    entity_state_set,
    is_wake_quiet,
    load_telegram_busy_disabled_chat_ids,
    load_telegram_privacy_from_db,
    save_telegram_busy_disabled_chat_ids,
    save_telegram_privacy_enforced,
    save_telegram_privacy_open,
)
from ngram.presence.tools.execution_rpc import (
    get_execution_client,
    self_update_host_permitted,
    self_update_tool_block_message,
)
from ngram.utils.git_info import git_head_info
from ngram.utils.self_update import perform_self_update

if TYPE_CHECKING:
    from ngram.entity import Entity

log = structlog.get_logger("ngram.presence.telegram")

# Telegram may redeliver the same update; bounded memory for seen update_id values.
_UPDATE_DEDUP_CAP = 8192
# (chat_id, message_id) — skip re-entrancy / redelivery before or during LLM work.
_MESSAGE_DEDUP_CAP = 8192
_OPERATOR_MENU_COMMANDS = frozenset({"private", "privacy", "update"})
_REMOTE_SESSION_MENU_COMMANDS = frozenset(
    {"session_start", "session_status", "session_stop"}
)

# Ephemeral busy indicator: each frame must differ (Telegram may no-op identical edits).
_BUSY_BRAILLE = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_BUSY_EDIT_INTERVAL_SEC = 0.9
# Pick a new random gerund after this many spinner edits (braille-only between rotations).
_BUSY_WORD_ROTATE_EVERY = 20

# ngram-themed spinner verbs (gerunds); braille animates between rotations.
_BUSY_ING_WORDS: tuple[str, ...] = (
    "absorbing",
    "adapting",
    "alighting",
    "attracting",
    "awakening",
    "balancing",
    "remembering",
    "bending",
    "biting",
    "blooming",
    "blossoming",
    "blowing",
    "breathing",
    "breeding",
    "budding",
    "building",
    "burrowing",
    "wondering",
    "capping",
    "carrying",
    "cementing",
    "chasing",
    "chewing",
    "circulating",
    "cleaning",
    "climbing",
    "clinging",
    "closing",
    "clustering",
    "collecting",
    "colonizing",
    "coloring",
    "communicating",
    "competing",
    "concentrating",
    "connecting",
    "cooling",
    "cooperating",
    "crawling",
    "creating",
    "crossing",
    "dancing",
    "decaying",
    "defending",
    "dehydrating",
    "descending",
    "discovering",
    "distributing",
    "diving",
    "drinking",
    "droning",
    "drooping",
    "dwelling",
    "emerging",
    "enchanting",
    "enduring",
    "enriching",
    "evolving",
    "excavating",
    "expanding",
    "exploring",
    "extracting",
    "fading",
    "fanning",
    "feeding",
    "fertilizing",
    "finding",
    "flapping",
    "flourishing",
    "flowering",
    "flowing",
    "flying",
    "following",
    "foraging",
    "forming",
    "fruiting",
    "gathering",
    "germinating",
    "gleaning",
    "glueing",
    "glowing",
    "growing",
    "guarding",
    "guiding",
    "handling",
    "harvesting",
    "hatching",
    "healing",
    "heating",
    "hibernating",
    "hiding",
    "hiving",
    "hovering",
    "humming",
    "hunting",
    "hybridizing",
    "identifying",
    "inhaling",
    "inspecting",
    "interacting",
    "inviting",
    "joining",
    "landing",
    "leading",
    "leafing",
    "learning",
    "licking",
    "lifting",
    "lingering",
    "living",
    "locating",
    "making",
    "managing",
    "manifesting",
    "marking",
    "mellifying",
    "migrating",
    "molting",
    "mounting",
    "navigating",
    "nesting",
    "nourishing",
    "nursing",
    "opening",
    "orienting",
    "overwintering",
    "packing",
    "partaking",
    "patrolling",
)


def _busy_indicator_html(frame: int, ing_word: str) -> str:
    """HTML for the busy line: monospace via <code> (Telegram HTML parse mode)."""
    spin = _BUSY_BRAILLE[frame % len(_BUSY_BRAILLE)]
    w = (ing_word or "").strip()
    if w:
        w = w.capitalize()
    inner = f"{spin}  {w}" if w else str(spin)
    return f"<code>{html.escape(inner)}</code>"


def _merge_telegram_ids(yaml_ids: object, env_name: str) -> set[int] | None:
    """Merge a YAML id list with a comma-separated environment variable."""
    from_yaml: set[int] = set()
    if isinstance(yaml_ids, list) and len(yaml_ids) > 0:
        for x in yaml_ids:
            try:
                from_yaml.add(int(x))
            except (TypeError, ValueError):
                pass
    raw_env = (os.environ.get(env_name) or "").strip()
    from_env: set[int] = set()
    if raw_env:
        for part in raw_env.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                from_env.add(int(part))
            except ValueError:
                pass
    out = from_yaml | from_env
    return out if out else None


def merge_telegram_allowed_user_ids(yaml_ids: object) -> set[int] | None:
    """Return the non-secret Telegram user allowlist from YAML and the environment."""
    return _merge_telegram_ids(yaml_ids, "NGRAM_TELEGRAM_ALLOWED_USER_IDS")


def merge_telegram_operator_user_ids(yaml_ids: object) -> set[int] | None:
    """Return Telegram privacy operators from YAML and the environment."""
    return _merge_telegram_ids(yaml_ids, "NGRAM_TELEGRAM_OPERATOR_IDS")


def _telegram_display_name(user: Any) -> str:
    """Disambiguate members in groups: full name + @username when available."""
    if user is None:
        return "User"
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    un = (getattr(user, "username", None) or "").strip()
    parts: list[str] = []
    if full:
        parts.append(full)
    if un:
        parts.append(f"@{un}")
    if parts:
        return " · ".join(parts)
    return str(getattr(user, "id", "") or "User")


class TelegramPlatform(Platform):
    def __init__(
        self,
        token: str,
        *,
        entity: Entity,
        app_version: str = "1.0.0",
        allowed_user_ids: set[int] | None = None,
        allowed_chat_ids: set[int] | None = None,
        operator_user_ids: set[int] | None = None,
        concurrent_updates: int = 1,
        poll_timeout: float = 20.0,
        poll_interval: float = 0.35,
        bot_name: str | None = None,
        bot_short_description: str | None = None,
        bot_description: str | None = None,
    ) -> None:
        self.token = token
        self._entity = entity
        self._app_version = app_version
        self._yaml_user_allow = allowed_user_ids
        self._yaml_chat_allow = allowed_chat_ids
        self._operator_user_ids = operator_user_ids
        entity_name = str(getattr(getattr(entity, "config", None), "name", "") or "ngram")
        self._bot_name = (bot_name or entity_name).strip()[:64]
        self._bot_short_description = (
            bot_short_description or f"{entity_name}. A persistent entity running on ngram."
        ).strip()[:120]
        self._bot_description = (
            bot_description
            or (
                f"{entity_name} remembers across conversations, acts through a persistent "
                "workspace, and runs on ngram."
            )
        ).strip()[:512]
        self._db_enforced = False
        self._db_user_allow: set[int] = set()
        self._db_chat_allow: set[int] = set()
        try:
            cu = int(concurrent_updates)
        except (TypeError, ValueError):
            cu = 1
        # PTB defaults to sequential update handling; allow bounded concurrency for busy group chats.
        self._concurrent_updates = max(1, min(256, cu))
        try:
            pt = float(poll_timeout)
        except (TypeError, ValueError):
            pt = 20.0
        self._poll_timeout = max(1.0, min(30.0, pt))
        try:
            pi = float(poll_interval)
        except (TypeError, ValueError):
            pi = 0.35
        self._poll_interval = max(0.0, min(2.0, pi))
        self.app = (
            Application.builder()
            .token(token)
            .concurrent_updates(self._concurrent_updates)
            .build()
        )
        self._cb: Callable[[Input], Any] | None = None
        self.last_chat_id: str | None = None
        self._denied_notified_chat_ids: set[int] = set()
        self._seen_update_ids: OrderedDict[int, None] = OrderedDict()
        self._update_dedup_lock = asyncio.Lock()
        self._processed_message_ids: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._message_id_dedup_lock = asyncio.Lock()
        self._chat_active_tasks: dict[int, asyncio.Task] = {}
        self._chat_active_tasks_lock = asyncio.Lock()
        self._remote_sessions: dict[int, dict[str, Any]] = {}
        self._remote_sessions_lock = asyncio.Lock()
        self._message_ring: list[dict[str, Any]] = []
        self._message_ring_cap = 200
        # Chats that opted out via /busy off (persisted in entity_state).
        self._busy_disabled_chats: set[int] = set()
        self._active_summon: dict[str, Any] | None = None
        try:
            _auto = entity.config.harness.autonomy
            _summon_cfg = getattr(_auto, "summon", None)
            self._summon_enabled = bool(_summon_cfg and getattr(_summon_cfg, "enabled", False))
            self._summon_timeout = float(getattr(_summon_cfg, "timeout_seconds", 30) or 30)
        except (AttributeError, TypeError):
            self._summon_enabled = False
            self._summon_timeout = 30.0

        self._register_handlers()

    def _register_handlers(self) -> None:
        # Single handler group: PTB runs at most one handler per update here. If /privacy lived in
        # group -1 while MessageHandler(filters.COMMAND) sat in group 0, both could fire — duplicate
        # replies and false "unknown command". Keep every handler in the same group (-1) and order
        # specific CommandHandlers before the COMMAND catch-all.
        _g = -1
        self.app.add_handler(CommandHandler("whoami", self._on_whoami), group=_g)
        self.app.add_handler(CommandHandler("private", self._on_private), group=_g)
        self.app.add_handler(CommandHandler("privacy", self._on_privacy), group=_g)
        self.app.add_handler(CommandHandler("start", self._on_start_command), group=_g)
        self.app.add_handler(CommandHandler("about", self._on_start_command), group=_g)
        self.app.add_handler(CommandHandler("help", self._on_help_command), group=_g)
        self.app.add_handler(CommandHandler("commands", self._on_commands_command), group=_g)
        self.app.add_handler(CommandHandler("status", self._on_status_command), group=_g)
        self.app.add_handler(CommandHandler("body", self._on_body_command), group=_g)
        self.app.add_handler(CommandHandler("journal", self._on_journal_command), group=_g)
        self.app.add_handler(CommandHandler("memories", self._on_memories_command), group=_g)
        # Common typo alias kept intentionally for UX.
        self.app.add_handler(CommandHandler("memeories", self._on_memories_command), group=_g)
        self.app.add_handler(CommandHandler("feelings", self._on_feelings_command), group=_g)
        self.app.add_handler(CommandHandler("me", self._on_me_command), group=_g)
        self.app.add_handler(CommandHandler("relationship", self._on_relationship_command), group=_g)
        self.app.add_handler(CommandHandler("models", self._on_models_command), group=_g)
        self.app.add_handler(CommandHandler("tools", self._on_tools_command), group=_g)
        self.app.add_handler(CommandHandler("routines", self._on_routines_command), group=_g)
        self.app.add_handler(CommandHandler("ping", self._on_ping_command), group=_g)
        self.app.add_handler(CommandHandler("version", self._on_version_command), group=_g)
        self.app.add_handler(CommandHandler("busy", self._on_busy_command), group=_g)
        self.app.add_handler(CommandHandler("wakequiet", self._on_wakequiet_command), group=_g)
        self.app.add_handler(CommandHandler("update", self._on_update_command), group=_g)
        self.app.add_handler(CommandHandler("context", self._on_context_command), group=_g)
        self.app.add_handler(CommandHandler("compact", self._on_compact_command), group=_g)
        self.app.add_handler(CommandHandler("reset", self._on_reset_command), group=_g)
        self.app.add_handler(CommandHandler("session_start", self._on_session_start), group=_g)
        self.app.add_handler(CommandHandler("session_status", self._on_session_status), group=_g)
        self.app.add_handler(CommandHandler("session_stop", self._on_session_stop), group=_g)
        self.app.add_handler(MessageHandler(filters.PHOTO, self._on_photo), group=_g)
        self.app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self._on_voice), group=_g)
        self.app.add_handler(MessageHandler(filters.Document.ALL, self._on_doc_image), group=_g)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text), group=_g)
        self.app.add_handler(MessageHandler(filters.COMMAND, self._on_unknown_command), group=_g)

    def _operators_configured(self) -> bool:
        return bool(self._operator_user_ids)

    def _menu_excluded_command_names(self, *, include_operator: bool = False) -> frozenset[str]:
        """Hide commands that the current deployment or menu audience cannot use."""
        excluded: set[str] = set()
        if not include_operator:
            excluded.update(_OPERATOR_MENU_COMMANDS)
        if not self._remote_sessions_enabled():
            excluded.update(_REMOTE_SESSION_MENU_COMMANDS)
        try:
            if not bool(self._entity.config.automations.enabled):
                excluded.add("routines")
        except (AttributeError, TypeError):
            excluded.add("routines")
        try:
            if not bool(self._entity.config.harness.autonomy.enabled):
                excluded.add("wakequiet")
        except (AttributeError, TypeError):
            excluded.add("wakequiet")
        return frozenset(excluded)

    def _is_operator(self, user_id: int | None) -> bool:
        if user_id is None or not self._operator_user_ids:
            return False
        return user_id in self._operator_user_ids

    async def _refresh_privacy_from_db(self) -> None:
        try:
            async with self._entity.store.session() as conn:
                enf, users, chats = await load_telegram_privacy_from_db(conn)
            self._db_enforced = enf
            self._db_user_allow = users
            self._db_chat_allow = chats
        except Exception as e:
            log.warning("telegram_privacy_load_failed", error=str(e))

    async def _refresh_busy_prefs_from_db(self) -> None:
        try:
            async with self._entity.store.session() as conn:
                disabled = await load_telegram_busy_disabled_chat_ids(conn)
            self._busy_disabled_chats = disabled
        except Exception as e:
            log.warning("telegram_busy_prefs_load_failed", error=str(e))

    def busy_indicator_enabled_for(self, chat_id: int) -> bool:
        """Whether to show the pinned busy line for this chat (see /busy)."""
        return int(chat_id) not in self._busy_disabled_chats

    def _allowed(self, update: Update) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return False
        if self._db_enforced:
            if not self._db_user_allow:
                return False
            if user is None or user.id not in self._db_user_allow:
                return False
            if self._db_chat_allow and chat.id not in self._db_chat_allow:
                return False
            return True
        if self._yaml_chat_allow is not None and chat.id not in self._yaml_chat_allow:
            return False
        if self._yaml_user_allow is not None:
            if user is None or user.id not in self._yaml_user_allow:
                return False
        return True

    async def _check_allowed(self, update: Update, *, notify: bool) -> bool:
        if self._allowed(update):
            return True
        if not notify:
            return False
        chat = update.effective_chat
        if chat is None:
            return False
        if chat.id in self._denied_notified_chat_ids:
            return False
        self._denied_notified_chat_ids.add(chat.id)
        try:
            await self.app.bot.send_message(
                chat_id=chat.id,
                text=format_access_denied(),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return False

    def _effective_tools_cfg(self) -> dict[str, Any]:
        base = self._entity.config.harness.tools if isinstance(self._entity.config.harness.tools, dict) else {}
        over = self._entity.config.raw.get("tools") if isinstance(self._entity.config.raw, dict) else {}
        if not isinstance(over, dict):
            over = {}
        merged = dict(base)
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return merged

    def _remote_session_cfg(self) -> dict[str, Any]:
        cfg = self._effective_tools_cfg().get("remote_session")
        return cfg if isinstance(cfg, dict) else {}

    def _remote_sessions_enabled(self) -> bool:
        cfg = self._remote_session_cfg()
        return bool(cfg.get("enabled", False))

    def _remote_session_refresh_seconds(self) -> float:
        cfg = self._remote_session_cfg()
        try:
            raw = float(cfg.get("refresh_seconds", 2.5))
        except (TypeError, ValueError):
            raw = 2.5
        return max(1.0, min(30.0, raw))

    def _remote_session_require_operator(self) -> bool:
        cfg = self._remote_session_cfg()
        if "require_operator" in cfg:
            return bool(cfg.get("require_operator"))
        return True

    def _remote_session_max_idle_seconds(self) -> int:
        cfg = self._remote_session_cfg()
        try:
            raw = int(cfg.get("max_idle_seconds", 900))
        except (TypeError, ValueError):
            raw = 900
        return max(60, min(86_400, raw))

    def _remote_session_snapshot(self, session: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(session, dict):
            return None
        keep = {
            "session_id",
            "status",
            "summary",
            "active_app",
            "last_action",
            "started_at",
            "last_refresh_at",
            "message_id",
            "chat_id",
        }
        return {k: session[k] for k in keep if k in session}

    def get_active_remote_session(self, channel: str) -> dict[str, Any] | None:
        try:
            chat_id = int(channel)
        except (TypeError, ValueError):
            return None
        return self._remote_session_snapshot(self._remote_sessions.get(chat_id))

    async def set_active_remote_session(
        self,
        channel: str,
        session: dict[str, Any] | None,
    ) -> None:
        try:
            chat_id = int(channel)
        except (TypeError, ValueError):
            return
        async with self._remote_sessions_lock:
            current = self._remote_sessions.get(chat_id)
            if session is None:
                if current is not None:
                    task = current.get("updater_task")
                    if isinstance(task, asyncio.Task):
                        task.cancel()
                    current.pop("updater_task", None)
                    current["status"] = "stopped"
                self._remote_sessions.pop(chat_id, None)
                return
            nxt = dict(current or {})
            nxt.update(session)
            nxt["chat_id"] = chat_id
            self._remote_sessions[chat_id] = nxt

    async def clear_remote_session_card(self, channel: str) -> None:
        try:
            chat_id = int(channel)
        except (TypeError, ValueError):
            return
        async with self._remote_sessions_lock:
            current = self._remote_sessions.get(chat_id)
            if current is None:
                return
            current.pop("message_id", None)

    def _remote_session_operator_allowed(self, update: Update) -> bool:
        if not self._remote_session_require_operator():
            return True
        user = update.effective_user
        return bool(user is not None and self._is_operator(int(user.id)))

    async def _ensure_remote_session_allowed(self, update: Update) -> bool:
        if not await self._check_allowed(update, notify=True):
            return False
        if not self._remote_sessions_enabled():
            await self._reply_html(update, format_session_disabled_html())
            return False
        if self._remote_session_require_operator() and not self._operators_configured():
            await self._reply_html(update, format_privacy_no_operators_html())
            return False
        if not self._remote_session_operator_allowed(update):
            await self._reply_html(update, format_session_operator_required_html())
            return False
        return True

    @staticmethod
    def _decode_remote_image_bytes(res: dict[str, Any]) -> bytes | None:
        raw = ""
        for key in ("image_base64", "screenshot_base64", "frame_base64"):
            val = res.get(key)
            if isinstance(val, str) and val.strip():
                raw = val.strip()
                break
        if not raw:
            return None
        try:
            return base64.b64decode(raw, validate=False)
        except Exception:
            return None

    async def _remote_session_rpc(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = get_execution_client()
        res = await client.call(action, payload)
        return res if isinstance(res, dict) else {"ok": False, "error": "invalid result"}

    def _remote_session_start_payload(self, update: Update) -> dict[str, Any]:
        chat = update.effective_chat
        user = update.effective_user
        return {
            "platform": "telegram",
            "chat_id": int(chat.id) if chat is not None else 0,
            "chat_type": str(getattr(chat, "type", "") or "").lower(),
            "entity_name": self._entity.config.name,
            "max_idle_seconds": self._remote_session_max_idle_seconds(),
            "requested_by": {
                "user_id": int(user.id) if user is not None else 0,
                "display_name": _telegram_display_name(user),
            },
        }

    async def _capture_remote_session(self, session_id: str) -> dict[str, Any]:
        return await self._remote_session_rpc(
            "desktop_session_capture",
            {"session_id": session_id, "format": "jpeg"},
        )

    async def _status_remote_session(self, session_id: str) -> dict[str, Any]:
        return await self._remote_session_rpc("desktop_session_status", {"session_id": session_id})

    async def upsert_remote_session_card(
        self,
        channel: str,
        *,
        image_bytes: bytes | None,
        caption: str,
        session: dict[str, Any] | None = None,
    ) -> None:
        try:
            chat_id = int(channel)
        except (TypeError, ValueError):
            return
        async with self._remote_sessions_lock:
            current = self._remote_sessions.setdefault(chat_id, {"chat_id": chat_id})
            if isinstance(session, dict):
                current.update(session)
            message_id = current.get("message_id")
            if image_bytes is not None:
                buf = io.BytesIO(image_bytes)
                buf.name = "remote-session.jpg"
                if message_id:
                    await self.app.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=int(message_id),
                        media=InputMediaPhoto(media=buf, caption=caption[:1024]),
                    )
                else:
                    msg = await self.app.bot.send_photo(chat_id=chat_id, photo=buf, caption=caption[:1024])
                    current["message_id"] = int(msg.message_id)
            elif message_id:
                await self.app.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    caption=caption[:1024],
                )
            current["last_refresh_at"] = time.time()

    async def _start_remote_session_updater(self, chat_id: int) -> None:
        async with self._remote_sessions_lock:
            current = self._remote_sessions.get(chat_id)
            if current is None:
                return
            task = current.get("updater_task")
            if isinstance(task, asyncio.Task) and not task.done():
                return
            current["updater_task"] = asyncio.create_task(self._remote_session_updater(chat_id))

    async def _remote_session_updater(self, chat_id: int) -> None:
        failures = 0
        while True:
            async with self._remote_sessions_lock:
                session = dict(self._remote_sessions.get(chat_id) or {})
            session_id = str(session.get("session_id") or "").strip()
            if not session_id:
                return
            try:
                status = await self._status_remote_session(session_id)
                if not status.get("ok"):
                    raise RuntimeError(str(status.get("error") or "status failed"))
                capture = await self._capture_remote_session(session_id)
                if not capture.get("ok"):
                    raise RuntimeError(str(capture.get("error") or "capture failed"))
                merged = dict(session)
                merged.update(status)
                merged.update({k: v for k, v in capture.items() if k != "image_base64"})
                caption = format_remote_session_caption(self._entity.config.name, merged)
                await self.upsert_remote_session_card(
                    str(chat_id),
                    image_bytes=self._decode_remote_image_bytes(capture),
                    caption=caption,
                    session=merged,
                )
                failures = 0
                if str(merged.get("status") or "").lower() in {"stopped", "ended", "closed"}:
                    await self.set_active_remote_session(str(chat_id), None)
                    return
                await asyncio.sleep(self._remote_session_refresh_seconds())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                log.warning("telegram_remote_session_refresh_failed", chat_id=chat_id, error=str(e))
                if failures >= 3:
                    return
                await asyncio.sleep(min(10.0, self._remote_session_refresh_seconds() * (failures + 1)))

    def _remember_last_chat(self, update: Update) -> int | None:
        chat = update.effective_chat
        if chat is None:
            return None
        self.last_chat_id = str(chat.id)
        return chat.id

    def _record_message_to_ring(self, update: Update) -> None:
        msg = update.message or update.edited_message
        if not msg:
            return
        bot_id, _ = self._bot_identity()
        from_user = getattr(msg, "from_user", None)
        if bot_id is not None and from_user is not None:
            try:
                if int(getattr(from_user, "id", 0) or 0) == bot_id:
                    return
            except (TypeError, ValueError):
                pass
        u = update.effective_user
        sender = _telegram_display_name(u) if u else "unknown"
        text = (msg.text or msg.caption or "").strip()
        if not text:
            return
        ts = msg.date.isoformat() if msg.date else ""
        self._message_ring.append({
            "channel": str(msg.chat_id),
            "sender": sender,
            "content": text[:500],
            "timestamp": ts,
        })
        if len(self._message_ring) > self._message_ring_cap:
            self._message_ring = self._message_ring[-self._message_ring_cap:]

    async def fetch_recent_messages(
        self,
        channel: str,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        ch = str(channel)
        matching = [m for m in self._message_ring if m["channel"] == ch]
        return matching[-limit:]

    # --- Summon system ---

    def open_summon(self, channel: str, user_id: str) -> None:
        """After responding in a group, stay present for follow-up messages."""
        if not self._summon_enabled:
            return
        self._active_summon = {
            "channel": str(channel),
            "participants": {str(user_id)},
            "opened_at": time.time(),
        }
        log.info("summon_opened", channel=channel, timeout=self._summon_timeout)

    def close_summon(self) -> None:
        if self._active_summon:
            log.info("summon_closed", channel=self._active_summon.get("channel"))
        self._active_summon = None

    def _refresh_summon(self) -> None:
        if self._active_summon:
            self._active_summon["opened_at"] = time.time()

    def _is_summon_active(self, channel: str) -> bool:
        if not self._active_summon:
            return False
        if str(self._active_summon["channel"]) != str(channel):
            return False
        elapsed = time.time() - float(self._active_summon.get("opened_at", 0))
        if elapsed > self._summon_timeout:
            self.close_summon()
            return False
        return True

    async def _check_summon_relevance(self, text: str, sender: str) -> bool:
        """Lightweight check: is this follow-up message meant for the entity?"""
        if not self._active_summon:
            return False
        if sender in self._active_summon.get("participants", set()):
            return True
        lower = text.lower()
        entity_name = self._entity.config.name.lower()
        if entity_name in lower:
            return True
        for word in ("you", "your", "yeah", "yes", "no", "what", "how", "why", "lol", "haha", "ok"):
            if lower.strip() == word or lower.startswith(word + " "):
                return True
        return False

    async def _take_update_if_fresh(self, update: Update) -> bool:
        """First delivery of ``update_id`` returns True; retries/duplicates return False."""
        async with self._update_dedup_lock:
            uid = update.update_id
            if uid in self._seen_update_ids:
                log.info("telegram_duplicate_update_skipped", update_id=uid)
                return False
            self._seen_update_ids[uid] = None
            while len(self._seen_update_ids) > _UPDATE_DEDUP_CAP:
                self._seen_update_ids.popitem(last=False)
            return True

    @staticmethod
    def _telegram_msg_meta(msg: Any) -> dict[str, Any]:
        return {
            "chat_type": str(getattr(msg.chat, "type", "") or "").lower(),
            "telegram_message_id": int(msg.message_id),
        }

    @staticmethod
    def _inject_custom_emojis_into_text(msg: Any, raw_text: str | None = None) -> str:
        text = raw_text or getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        entities = list(getattr(msg, "entities", None) or [])
        entities += list(getattr(msg, "caption_entities", None) or [])

        custom_emojis = [e for e in entities if getattr(e, "type", "") == "custom_emoji"]
        if not custom_emojis or not text:
            return text

        try:
            custom_emojis.sort(key=lambda x: getattr(x, "offset", 0), reverse=True)
            text_utf16 = text.encode("utf-16-le")

            injected_any = False
            for ent in custom_emojis:
                off = int(getattr(ent, "offset", 0) or 0) * 2
                ln = int(getattr(ent, "length", 0) or 0) * 2
                eid = str(getattr(ent, "custom_emoji_id", "") or "")

                if not eid:
                    continue

                prefix = text_utf16[:off]
                emoji_utf16 = text_utf16[off:off+ln]
                suffix = text_utf16[off+ln:]

                emoji_str = emoji_utf16.decode("utf-16-le")
                replacement = f'<tg-emoji emoji-id="{eid}">{emoji_str}</tg-emoji>'

                text_utf16 = prefix + replacement.encode("utf-16-le") + suffix
                injected_any = True

            final_text = text_utf16.decode("utf-16-le")
            if injected_any:
                final_text += (
                    "\n\n[System Note: The user's message contained custom Telegram emojis. "
                    "You can output these exact HTML tags (e.g. <tg-emoji emoji-id=\"...\">...</tg-emoji>) "
                    "in your reply to echo or use them!]"
                )
            return final_text
        except Exception as e:
            log.warning("telegram_emoji_inject_failed", error=str(e))
            return text

    def _bot_identity(self) -> tuple[int | None, str]:
        """(bot_id, @username-lower) best effort; safe before full app init."""
        try:
            me = self.app.bot
        except Exception:
            return None, ""
        bot_id: int | None = None
        username = ""
        try:
            bot_id = int(getattr(me, "id", 0) or 0) or None
        except Exception:
            bot_id = None
        try:
            username = str(getattr(me, "username", "") or "").strip().lower()
        except Exception:
            username = ""
        return bot_id, username

    def _telegram_message_mentions_bot(self, msg: Any, text: str) -> bool:
        bot_id, bot_un = self._bot_identity()
        entities = list(getattr(msg, "entities", None) or [])
        entities += list(getattr(msg, "caption_entities", None) or [])
        for ent in entities:
            et = str(getattr(ent, "type", "") or "").lower()
            if et == "text_mention":
                u = getattr(ent, "user", None)
                if u is not None and bot_id is not None and int(getattr(u, "id", 0) or 0) == bot_id:
                    return True
            if et == "mention":
                off = int(getattr(ent, "offset", 0) or 0)
                ln = int(getattr(ent, "length", 0) or 0)
                token = (text or "")[off : off + ln].strip().lstrip("@").lower()
                if bot_un and token == bot_un:
                    return True
        # Fallback for plain-text mention when entities were stripped/omitted.
        if bot_un and f"@{bot_un}" in (text or "").lower():
            return True
        return False

    def _telegram_message_replies_to_bot(self, msg: Any) -> bool:
        bot_id, _ = self._bot_identity()
        if bot_id is None:
            return False
        parent = getattr(msg, "reply_to_message", None)
        if parent is None:
            return False
        from_user = getattr(parent, "from_user", None)
        if from_user is None:
            return False
        try:
            return int(getattr(from_user, "id", 0) or 0) == bot_id
        except Exception:
            return False

    def _should_respond_to_group_message(self, msg: Any, text: str) -> bool:
        """Group/supergroup/channel: only mention or reply-to-bot."""
        chat_type = str(getattr(getattr(msg, "chat", None), "type", "") or "").lower()
        if chat_type in ("private",):
            return True
        return self._telegram_message_mentions_bot(msg, text) or self._telegram_message_replies_to_bot(msg)

    def _telegram_msg_meta_with_session(self, msg: Any) -> dict[str, Any]:
        meta = self._telegram_msg_meta(msg)
        session = self.get_active_remote_session(str(getattr(msg, "chat_id", "") or ""))
        if isinstance(session, dict):
            meta["desktop_session"] = session
        return meta

    async def _cancel_active_chat_task(self, chat_id: int) -> None:
        """Cancel any in-flight perceive task for this chat so the new message is handled immediately."""
        async with self._chat_active_tasks_lock:
            old = self._chat_active_tasks.get(chat_id)
            if old is not None and not old.done():
                log.info("telegram_interrupting_active_task", chat_id=chat_id)
                old.cancel()
                try:
                    await old
                except (asyncio.CancelledError, Exception):
                    pass
            self._chat_active_tasks.pop(chat_id, None)

    async def _claim_telegram_message(self, chat_id: int, message_id: int) -> bool:
        key = (chat_id, message_id)
        async with self._message_id_dedup_lock:
            if key in self._processed_message_ids:
                log.info(
                    "telegram_duplicate_message_skipped",
                    chat_id=chat_id,
                    message_id=message_id,
                )
                return False
            self._processed_message_ids[key] = None
            while len(self._processed_message_ids) > _MESSAGE_DEDUP_CAP:
                self._processed_message_ids.popitem(last=False)
            return True

    async def _release_telegram_message_claim(self, chat_id: int, message_id: int) -> None:
        key = (chat_id, message_id)
        async with self._message_id_dedup_lock:
            self._processed_message_ids.pop(key, None)

    async def _run_callback(self, inp: Input) -> None:
        if not self._cb:
            return
        meta = inp.metadata if isinstance(inp.metadata, dict) else {}
        mid_raw = meta.get("telegram_message_id")
        cid_raw = (inp.channel or "").strip()
        key: tuple[int, int] | None = None
        if mid_raw is not None and cid_raw:
            try:
                key = (int(cid_raw), int(mid_raw))
            except (TypeError, ValueError):
                key = None

        if key is not None and not await self._claim_telegram_message(key[0], key[1]):
            return

        # Cancel any in-flight task for this chat — new message takes priority.
        chat_id: int | None = None
        if cid_raw:
            try:
                chat_id = int(cid_raw)
            except (TypeError, ValueError):
                chat_id = None

        async def _invoke() -> None:
            if chat_id is not None:
                await self._cancel_active_chat_task(chat_id)
            try:
                await self._cb(inp)
            except asyncio.CancelledError:
                log.info("telegram_task_interrupted", channel=cid_raw)
            except Exception as e:
                if key is not None:
                    await self._release_telegram_message_claim(key[0], key[1])
                log.warning("telegram_callback_failed", error=str(e))
            finally:
                # Clean up tracking when task completes or is cancelled.
                if chat_id is not None:
                    async with self._chat_active_tasks_lock:
                        current = self._chat_active_tasks.get(chat_id)
                        if current is not None and current is task:
                            self._chat_active_tasks.pop(chat_id, None)

        task = asyncio.create_task(_invoke())
        if chat_id is not None:
            async with self._chat_active_tasks_lock:
                self._chat_active_tasks[chat_id] = task

    async def _send_html_chunks(self, chat_id: int, html_text: str, *, pause: float = 0.0) -> None:
        chunks = split_telegram_chunks(html_text, limit=3900)
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if pause > 0 and i < len(chunks) - 1:
                await asyncio.sleep(pause)

    async def _reply_html(self, update: Update, html_text: str, *, pause: float = 0.0) -> None:
        chat_id = self._remember_last_chat(update)
        if chat_id is None:
            return
        await self._send_html_chunks(chat_id, html_text, pause=pause)

    @staticmethod
    def _parse_commands_args(args: list[str]) -> tuple[int, str | None]:
        if not args:
            return 0, None
        page = 0
        query: str | None = None
        first = args[0].strip()
        if first.lstrip("+-").isdigit():
            try:
                page = max(0, int(first) - 1)
            except ValueError:
                page = 0
            if len(args) > 1:
                query = " ".join(a for a in args[1:] if a.strip()).strip() or None
        else:
            query = " ".join(a for a in args if a.strip()).strip() or None
        return page, query

    @staticmethod
    def _parse_memories_count_or_error(
        args: list[str],
        *,
        default: int = 5,
        min_count: int = 1,
        max_count: int = 12,
        example_command: str = "/memories 5",
    ) -> tuple[int, str | None]:
        if not args:
            return default, None
        raw = (args[0] or "").strip()
        if not raw:
            return default, None
        if not raw.lstrip("+-").isdigit():
            example = html.escape(example_command)
            return default, f"Count must be a number. Example: <code>{example}</code>."
        try:
            val = int(raw)
        except ValueError:
            example = html.escape(example_command)
            return default, f"Count must be a number. Example: <code>{example}</code>."
        if val < min_count or val > max_count:
            return default, f"Count must be between <code>{min_count}</code> and <code>{max_count}</code>."
        return val, None

    @staticmethod
    def _parse_compact_args(args: list[str]) -> tuple[str, bool, int]:
        """Return (action, aggressive, passes). action in {'run', 'status', 'usage'}."""
        if not args:
            return ("run", False, 1)
        a0 = str(args[0] or "").strip().lower()
        if a0 in {"status", "stats"}:
            return ("status", False, 1)
        if a0 in {"help", "?"}:
            return ("usage", False, 1)
        if a0 in {"now", "run"}:
            return ("run", False, 1)
        # Backwards-compatible aliases from earlier compaction behavior.
        if a0 in {"aggressive", "hard", "passes", "pass"}:
            return ("run", False, 1)
        if a0.lstrip("+-").isdigit():
            return ("run", False, 1)
        return ("usage", False, 1)

    async def _on_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        _ = context
        u = update.effective_user
        if u is None or update.effective_chat is None:
            return
        name = _telegram_display_name(u)
        un = (getattr(u, "username", None) or "").strip() or None
        await self._reply_html(
            update,
            format_whoami_html(user_id=int(u.id), full_name=name, username=un),
        )

    async def _privacy_apply_lock(self, update: Update) -> None:
        if not self._operator_user_ids:
            await self._reply_html(update, format_privacy_no_operators_html())
            return
        try:
            async with self._entity.store.session() as conn:
                await save_telegram_privacy_enforced(
                    conn,
                    user_ids=set(self._operator_user_ids),
                    chat_ids=set(),
                )
            await self._refresh_privacy_from_db()
            await self._reply_html(
                update,
                format_privacy_locked_html(len(self._db_user_allow)),
            )
        except Exception as e:
            log.warning("telegram_privacy_lock_failed", error=str(e))
            await self._reply_html(update, f"Could not lock: {html.escape(str(e)[:200])}")

    async def _privacy_apply_open(self, update: Update) -> None:
        try:
            async with self._entity.store.session() as conn:
                await save_telegram_privacy_open(conn)
            await self._refresh_privacy_from_db()
            await self._reply_html(update, format_privacy_opened_html())
        except Exception as e:
            log.warning("telegram_privacy_open_failed", error=str(e))
            await self._reply_html(update, f"Could not open: {html.escape(str(e)[:200])}")

    async def _on_private(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        args = [str(a).strip().lower() for a in (context.args or []) if str(a).strip()]
        u = update.effective_user
        if u is None or update.effective_chat is None:
            return
        uid = int(u.id)
        word = args[0] if args else ""
        if word not in ("on", "off"):
            await self._reply_html(update, format_private_usage_html())
            return
        if not self._operators_configured():
            await self._reply_html(update, format_privacy_no_operators_html())
            return
        if not self._is_operator(uid):
            await self._reply_html(update, format_privacy_operator_required_html())
            return
        if word == "on":
            await self._privacy_apply_lock(update)
        else:
            await self._privacy_apply_open(update)

    async def _on_privacy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        args = [a.strip() for a in (context.args or []) if str(a).strip()]
        sub = (args[0] or "status").lower() if args else "status"
        if sub in ("help", "?"):
            await self._reply_html(update, format_privacy_help_html())
            return

        u = update.effective_user
        if u is None or update.effective_chat is None:
            return
        uid = int(u.id)

        yaml_u = self._yaml_user_allow is not None
        ops_ok = self._operators_configured()

        if sub == "status":
            await self._reply_html(
                update,
                format_privacy_status_html(
                    enforced=self._db_enforced,
                    allowed_ids=sorted(self._db_user_allow),
                    yaml_restricted=yaml_u,
                    operators_configured=ops_ok,
                ),
            )
            return

        if not ops_ok:
            await self._reply_html(update, format_privacy_no_operators_html())
            return
        if not self._is_operator(uid):
            await self._reply_html(update, format_privacy_operator_required_html())
            return

        if sub == "lock":
            await self._privacy_apply_lock(update)
            return

        if sub == "open":
            await self._privacy_apply_open(update)
            return

        if sub in ("allow", "deny"):
            if len(args) < 2:
                await self._reply_html(update, format_privacy_allow_deny_usage_html())
                return
            try:
                tid = int(args[1].strip())
            except ValueError:
                await self._reply_html(update, format_privacy_invalid_id_html())
                return
            if not self._db_enforced:
                await self._reply_html(
                    update,
                    "Use <code>/private on</code> or <code>/privacy lock</code> first, then add or remove user ids.",
                )
                return
            try:
                async with self._entity.store.session() as conn:
                    nxt = set(self._db_user_allow)
                    if sub == "allow":
                        nxt.add(tid)
                    else:
                        nxt.discard(tid)
                        if not nxt:
                            await self._reply_html(update, format_privacy_cannot_deny_last_html())
                            return
                    await save_telegram_privacy_enforced(conn, user_ids=nxt, chat_ids=set())
                await self._refresh_privacy_from_db()
                await self._reply_html(
                    update,
                    f"{'Added' if sub == 'allow' else 'Removed'} <code>{tid}</code>. "
                    f"{len(self._db_user_allow)} id(s) allowed.",
                )
            except Exception as e:
                log.warning("telegram_privacy_allow_deny_failed", error=str(e))
                await self._reply_html(update, f"Could not update: {html.escape(str(e)[:200])}")
            return

        await self._reply_html(update, format_unknown_command(f"/privacy {sub}"))

    async def _on_start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        u = update.effective_user
        first_name = u.first_name if u else None
        await self._reply_html(
            update,
            format_start_html(self._entity.config.name, self._app_version, first_name=first_name),
        )

    async def _on_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        await self._reply_html(update, format_help_html(self._entity.config.name))

    async def _on_commands_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        args = list(context.args or [])
        page, query = self._parse_commands_args(args)
        user = update.effective_user
        include_operator = bool(user is not None and self._is_operator(int(user.id)))
        body, _, _ = format_commands_page(
            page,
            query=query,
            excluded_names=self._menu_excluded_command_names(include_operator=include_operator),
        )
        await self._reply_html(update, body)

    async def _on_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        body = await build_status_html(self._entity, self._app_version)
        await self._reply_html(update, body)

    async def _on_body_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Serve ``soma/body.md`` directly from the local workspace (written by the worker)."""
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context

        from pathlib import Path
        body_path = Path(self._entity.config.soma_dir()) / "body.md"
        try:
            if body_path.is_file():
                text = body_path.read_text(encoding="utf-8")
            else:
                text = "(body.md not found in local soma directory)"
        except Exception as e:
            await self._reply_html(
                update,
                "<b>Could not read soma/body.md</b>\n<pre>" + html.escape(str(e)[:4000]) + "</pre>",
            )
            return

        chat_id = self._remember_last_chat(update)
        if chat_id is None:
            return
        frags = format_body_md_reply_html_chunks(text)
        for i, frag in enumerate(frags):
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=frag,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if i < len(frags) - 1:
                await asyncio.sleep(0.06)

    async def _on_journal_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Serve recent journal entries directly from canonical state, without a model pass."""
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return

        args = list(context.args or [])
        limit, err = self._parse_memories_count_or_error(
            args,
            default=5,
            min_count=1,
            max_count=12,
            example_command="/journal 5",
        )
        if err:
            await self._reply_html(
                update,
                "<b>Journal</b>\n\n"
                f"{err}\n"
                "Usage: <code>/journal [count]</code>.",
            )
            return

        journal_path = Path(self._entity.config.journal_path()).expanduser()
        try:
            text = (
                journal_path.read_text(encoding="utf-8", errors="replace")
                if journal_path.is_file()
                else ""
            )
        except OSError as exc:
            await self._reply_html(
                update,
                "<b>Could not read the journal</b>\n"
                f"<code>{html.escape(str(exc)[:1000])}</code>",
            )
            return

        chat_id = self._remember_last_chat(update)
        if chat_id is None:
            return
        fragments = format_journal_html_chunks(
            text,
            self._entity.config.name,
            requested_count=limit,
        )
        for index, fragment in enumerate(fragments):
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=fragment,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if index < len(fragments) - 1:
                await asyncio.sleep(0.06)

    async def _on_memories_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        args = list(context.args or [])
        limit, err = self._parse_memories_count_or_error(args, default=5, min_count=1, max_count=12)
        if err:
            await self._reply_html(
                update,
                "<b>Memories</b>\n\n"
                f"{err}\n"
                "Usage: <code>/memories [count]</code> (alias: <code>/memeories</code>).",
            )
            return
        memories = await self._entity.fetch_cli_real_memories(limit)
        body = format_memories_html(self._entity.config.name, memories, requested_count=limit)
        await self._reply_html(update, body)

    async def _on_feelings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        body = format_feelings_html(self._entity.config.name, self._entity.emotions.get_state())
        await self._reply_html(update, body)

    async def _on_me_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        args = [str(a).strip() for a in (context.args or []) if str(a).strip()]
        u = update.effective_user
        person_id = str(u.id) if u else "unknown"
        target_label = _telegram_display_name(u) if u else "you"
        if args:
            target = " ".join(args).strip()
            route = self._entity.resolve_person_route(target, platform="telegram", prefer_private=True)
            if route and str(route.get("person_id") or "").strip():
                person_id = str(route.get("person_id"))
                target_label = str(route.get("person_name") or target_label)
        async with self._entity.store.session() as db:
            rel = await self._entity.relational.get(db, person_id)
            # Try to fetch the deep relational document
            rel_doc = None
            try:
                rel_doc_mem = getattr(self._entity, "relational_docs", None)
                if rel_doc_mem is not None:
                    rel_doc = await rel_doc_mem.get(db, person_id)
            except Exception:
                pass
        # Fetch somatic marker if available
        somatic_marker = None
        try:
            bars = getattr(self._entity.tonic, "bars", None)
            markers = getattr(bars, "_somatic_markers", {}) if bars else {}
            if person_id in markers:
                somatic_marker = dict(markers[person_id])
        except Exception:
            pass
        await self._reply_html(
            update,
            format_me_html(
                self._entity.config.name,
                rel,
                target_label=target_label,
                relational_doc=rel_doc,
                somatic_marker=somatic_marker,
            ),
        )

    async def _on_relationship_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        args = [str(a).strip() for a in (context.args or []) if str(a).strip()]
        u = update.effective_user
        person_id = str(u.id) if u else "unknown"
        target_label = _telegram_display_name(u) if u else "you"
        if args:
            target = " ".join(args).strip()
            route = self._entity.resolve_person_route(target, platform="telegram", prefer_private=True)
            if route and str(route.get("person_id") or "").strip():
                person_id = str(route.get("person_id"))
                target_label = str(route.get("person_name") or target_label)
        async with self._entity.store.session() as db:
            rel = await self._entity.relational.get(db, person_id)
            rel_doc = None
            try:
                rel_doc_mem = getattr(self._entity, "relational_docs", None)
                if rel_doc_mem is not None:
                    rel_doc = await rel_doc_mem.get(db, person_id)
            except Exception:
                pass
        somatic_marker = None
        try:
            bars = getattr(self._entity.tonic, "bars", None)
            markers = getattr(bars, "_somatic_markers", {}) if bars else {}
            if person_id in markers:
                somatic_marker = dict(markers[person_id])
        except Exception:
            pass
        await self._reply_html(
            update,
            format_relationship_html(
                self._entity.config.name,
                rel,
                rel_doc,
                target_label=target_label,
                somatic_marker=somatic_marker,
            ),
        )

    async def _on_models_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        await self._reply_html(update, format_models_html(self._entity))

    async def _on_tools_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        page, query = self._parse_commands_args(list(context.args or []))
        body, _, _ = format_tools_html(self._entity, page=page, query=query)
        await self._reply_html(update, body)

    async def _on_routines_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        rows = await self._entity.store.list_automations(enabled_only=False)
        eng = getattr(self._entity, "automation_engine", None)
        body = format_routines_html(
            self._entity.config.name,
            rows,
            automations_enabled=bool(self._entity.config.automations.enabled),
            scheduler_ready=eng is not None,
        )
        await self._reply_html(update, body)

    async def _on_ping_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        await self._reply_html(update, format_ping_html(self._app_version))

    async def _on_version_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        short, subj = git_head_info()
        await self._reply_html(
            update,
            format_version_html(
                self._app_version,
                commit_short=short,
                commit_subject=subj,
            ),
        )

    async def _on_busy_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        msg = update.effective_message
        if not msg:
            return
        chat_id = int(msg.chat_id)
        args = [str(a).strip().lower() for a in (context.args or []) if str(a).strip()]
        sub = args[0] if args else "toggle"
        if sub in ("status", "st", "?"):
            await self._reply_html(
                update,
                format_busy_status_html(enabled=self.busy_indicator_enabled_for(chat_id)),
            )
            return
        if sub in ("toggle", "t"):
            if chat_id in self._busy_disabled_chats:
                self._busy_disabled_chats.discard(chat_id)
            else:
                self._busy_disabled_chats.add(chat_id)
        elif sub in ("on", "enable", "yes", "true", "1"):
            self._busy_disabled_chats.discard(chat_id)
        elif sub in ("off", "disable", "no", "false", "0"):
            self._busy_disabled_chats.add(chat_id)
        else:
            await self._reply_html(
                update,
                format_unknown_command(f"/busy {' '.join(args)}"),
            )
            return
        try:
            async with self._entity.store.session() as conn:
                await save_telegram_busy_disabled_chat_ids(conn, self._busy_disabled_chats)
        except Exception as e:
            log.warning("telegram_busy_prefs_save_failed", error=str(e))
            await self._reply_html(
                update,
                "<b>Could not save preference.</b> Try again in a moment.",
            )
            return
        await self._reply_html(
            update,
            format_busy_status_html(enabled=self.busy_indicator_enabled_for(chat_id)),
        )

    async def _on_wakequiet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        msg = update.effective_message
        if not msg:
            return
        args = [str(a).strip().lower() for a in (context.args or []) if str(a).strip()]
        sub = args[0] if args else "status"
        auto = self._entity.config.harness.autonomy
        yaml_s = bool(getattr(auto, "wake_user_visible_status", True))
        yaml_t = bool(getattr(auto, "wake_chat_tool_activity", True))

        if sub in ("help", "h"):
            await self._reply_html(
                update,
                "<b>/wakequiet</b> — whether autonomous <i>wake</i> status lines and per-tool lines "
                "are mirrored to Telegram or only written to the autonomy transcript.\n\n"
                "• <code>/wakequiet on</code> — hide those lines from chat (transcript only)\n"
                "• <code>/wakequiet off</code> — follow your entity YAML again\n"
                "• <code>/wakequiet status</code> — current mode\n\n"
                "Normal replies during a wake still post as usual; this only affects italic "
                "status / tool-activity lines.",
            )
            return

        if sub in ("status", "st", "?"):
            try:
                async with self._entity.store.session() as conn:
                    q = await is_wake_quiet(conn)
            except Exception as e:
                log.warning("wakequiet_status_failed", error=str(e))
                await self._reply_html(update, "<b>Could not read preference.</b> Try again shortly.")
                return
            await self._reply_html(
                update,
                format_wakequiet_status_html(
                    quiet_db=q, yaml_status_mirror=yaml_s, yaml_tools_mirror=yaml_t,
                ),
            )
            return

        if sub in ("on", "enable", "yes", "true", "1", "quiet"):
            try:
                async with self._entity.store.session() as conn:
                    await entity_state_set(conn, KEY_WAKE_QUIET, "1")
            except Exception as e:
                log.warning("wakequiet_save_failed", error=str(e))
                await self._reply_html(
                    update,
                    "<b>Could not save preference.</b> Try again in a moment.",
                )
                return
        elif sub in ("off", "disable", "no", "false", "0"):
            try:
                async with self._entity.store.session() as conn:
                    await entity_state_delete(conn, KEY_WAKE_QUIET)
            except Exception as e:
                log.warning("wakequiet_save_failed", error=str(e))
                await self._reply_html(
                    update,
                    "<b>Could not save preference.</b> Try again in a moment.",
                )
                return
        else:
            await self._reply_html(
                update,
                format_unknown_command(f"/wakequiet {' '.join(args)}"),
            )
            return

        try:
            async with self._entity.store.session() as conn:
                q = await is_wake_quiet(conn)
        except Exception as e:
            log.warning("wakequiet_status_failed", error=str(e))
            await self._reply_html(update, "<b>Saved, but could not re-read status.</b>")
            return
        await self._reply_html(
            update,
            format_wakequiet_status_html(quiet_db=q, yaml_status_mirror=yaml_s, yaml_tools_mirror=yaml_t),
        )

    async def _on_update_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        u = update.effective_user
        uid = int(u.id) if u else None
        if not self._operators_configured():
            await self._reply_html(update, format_privacy_no_operators_html())
            return
        if not self._is_operator(uid):
            await self._reply_html(update, format_privacy_operator_required_html())
            return
        if not self_update_host_permitted(self._entity):
            await self._reply_html(
                update,
                format_update_blocked_html(self_update_tool_block_message(self._entity)),
            )
            return
        raw_args = [str(a).strip().lower() for a in (context.args or []) if str(a).strip()]
        skip_pip = any(x in ("no-pip", "nopip", "no_pip") for x in raw_args)
        log_lines: list[str] = []

        def _log(msg: str) -> None:
            log_lines.append(msg)

        await self._reply_html(
            update,
            "<b>Updating ngram…</b>\n"
            "Pulling from the public repo (may take a minute).",
        )
        result = await asyncio.to_thread(
            perform_self_update,
            pip_reinstall=not skip_pip,
            dry_run=False,
            log=_log,
        )
        await self._reply_html(update, format_update_result_html(result, log_lines=log_lines))

    async def _on_context_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        ent = self._entity
        cfg = ent.config
        max_ctx = int(cfg.cognition.max_context_tokens or cfg.harness.cognition.max_context_tokens)
        history = getattr(ent, "_history", [])
        summary = (getattr(ent, "_history_rolling_summary", "") or "").strip()

        history_chars = sum(len(str(m.get("content", ""))) for m in history)
        summary_chars = len(summary)
        history_tokens_est = (history_chars + summary_chars) // 4

        soma_body = ent.tonic.render_body() if hasattr(ent, "tonic") else ""
        soma_tokens_est = len(soma_body) // 4

        sys_prompt_est = int(cfg.cognition.system_prompt_char_limit or 12000) // 4
        total_est = history_tokens_est + soma_tokens_est + sys_prompt_est
        remaining_est = max(0, max_ctx - total_est)
        pct_used = min(100, int(total_est / max_ctx * 100)) if max_ctx > 0 else 0

        bar_width = 20
        filled = int(bar_width * pct_used / 100)
        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)

        body = (
            f"<b>Context window</b>\n\n"
            f"<code>{bar} {pct_used}%</code>\n\n"
            f"<b>Budget:</b> {max_ctx:,} tokens\n"
            f"<b>Estimated used:</b> ~{total_est:,}\n"
            f"<b>Estimated remaining:</b> ~{remaining_est:,}\n\n"
            f"<b>Breakdown:</b>\n"
            f"  System prompt: ~{sys_prompt_est:,} tokens\n"
            f"  Conversation: ~{history_tokens_est:,} tokens ({len(history)} messages)\n"
            f"  Body state: ~{soma_tokens_est:,} tokens\n"
        )
        if summary:
            body += f"  Rolling summary: ~{summary_chars // 4:,} tokens\n"
        body += (
            f"\n<b>Config:</b>\n"
            f"  max_context_tokens: {max_ctx:,}\n"
            f"  deliberate_max_tokens: {int(cfg.cognition.deliberate_max_tokens or cfg.harness.cognition.deliberate_max_tokens):,}\n"
            f"  reflex_max_tokens: {cfg.harness.cognition.reflex_max_tokens:,}\n"
            f"  history messages: {len(history)} / {cfg.cognition.rolling_history_max_messages}\n"
            f"  compression: {'on' if cfg.cognition.history_compression.enabled else 'off'}"
        )
        await self._reply_html(update, body)

    async def _on_compact_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        args = [str(a).strip() for a in (context.args or []) if str(a).strip()]
        action, aggressive, passes = self._parse_compact_args(args)

        ent = self._entity
        if action == "usage":
            await self._reply_html(
                update,
                (
                    "<b>Context refresh</b>\n\n"
                    "Usage:\n"
                    "• <code>/compact</code> — clear rolling chat turns now\n"
                    "• <code>/compact status</code> — show current history footprint\n\n"
                    "This refreshes the live context window. Persistent SQLite memories are untouched."
                ),
            )
            return

        history = getattr(ent, "_history", [])
        summary = (getattr(ent, "_history_rolling_summary", "") or "").strip()
        summary_tokens = len(summary) // 4
        if action == "status":
            await self._reply_html(
                update,
                (
                    "<b>Compaction status</b>\n\n"
                    f"• History messages: <code>{len(history)}</code>\n"
                    f"• Rolling summary: <code>{len(summary):,}</code> chars (~{summary_tokens:,} tokens)\n"
                    f"• Compression: <code>{'on' if ent.config.cognition.history_compression.enabled else 'off'}</code>\n"
                    f"• Threshold ratio: <code>{ent.config.cognition.history_compression.compaction_threshold_ratio:.2f}</code>\n"
                    f"• Max passes: <code>{ent.config.cognition.history_compression.compaction_max_passes}</code>"
                ),
            )
            return

        _ = aggressive, passes
        self._entity.clear_conversation_history()
        await self._reply_html(update, format_reset_html(self._entity.config.name))

    async def _on_reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        self._entity.clear_conversation_history()
        await self._reply_html(update, format_reset_html(self._entity.config.name))

    async def _on_session_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        _ = context
        if update.effective_chat is None:
            return
        if not await self._ensure_remote_session_allowed(update):
            return
        chat_id = int(update.effective_chat.id)
        self._remember_last_chat(update)
        existing = self.get_active_remote_session(str(chat_id))
        if isinstance(existing, dict) and str(existing.get("session_id") or "").strip():
            await self._reply_html(update, format_session_status_html(self._entity.config.name, existing))
            return
        start_res = await self._remote_session_rpc(
            "desktop_session_start",
            self._remote_session_start_payload(update),
        )
        if not start_res.get("ok"):
            await self._reply_html(
                update,
                f"Could not start a remote session: {html.escape(str(start_res.get('error') or '')[:300])}",
            )
            return
        session = dict(start_res)
        session["chat_id"] = chat_id
        session["status"] = str(session.get("status") or "running")
        await self.set_active_remote_session(str(chat_id), session)
        session_id = str(session.get("session_id") or "").strip()
        if session_id:
            capture = await self._capture_remote_session(session_id)
            if capture.get("ok"):
                session.update({k: v for k, v in capture.items() if k != "image_base64"})
                await self.set_active_remote_session(str(chat_id), session)
                await self.upsert_remote_session_card(
                    str(chat_id),
                    image_bytes=self._decode_remote_image_bytes(capture),
                    caption=format_remote_session_caption(self._entity.config.name, session),
                    session=session,
                )
        await self._start_remote_session_updater(chat_id)
        await self._reply_html(update, format_session_status_html(self._entity.config.name, session))

    async def _on_session_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        _ = context
        if update.effective_chat is None:
            return
        if not await self._ensure_remote_session_allowed(update):
            return
        chat_id = int(update.effective_chat.id)
        self._remember_last_chat(update)
        session = self.get_active_remote_session(str(chat_id))
        if not isinstance(session, dict):
            await self._reply_html(update, format_session_status_html(self._entity.config.name, None))
            return
        session_id = str(session.get("session_id") or "").strip()
        if session_id:
            status = await self._status_remote_session(session_id)
            if status.get("ok"):
                session.update(status)
                await self.set_active_remote_session(str(chat_id), session)
        await self._reply_html(update, format_session_status_html(self._entity.config.name, session))

    async def _on_session_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        _ = context
        if update.effective_chat is None:
            return
        if not await self._ensure_remote_session_allowed(update):
            return
        chat_id = int(update.effective_chat.id)
        self._remember_last_chat(update)
        session = self.get_active_remote_session(str(chat_id))
        if not isinstance(session, dict):
            await self._reply_html(update, format_session_status_html(self._entity.config.name, None))
            return
        session_id = str(session.get("session_id") or "").strip()
        if session_id:
            stop_res = await self._remote_session_rpc("desktop_session_stop", {"session_id": session_id})
            if not stop_res.get("ok"):
                await self._reply_html(
                    update,
                    f"Could not stop the remote session: {html.escape(str(stop_res.get('error') or '')[:300])}",
                )
                return
        await self.set_active_remote_session(str(chat_id), None)
        await self._reply_html(update, format_session_status_html(self._entity.config.name, None))

    async def _on_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not await self._check_allowed(update, notify=True):
            return
        _ = context
        msg = update.effective_message
        txt = (msg.text if msg else "").strip()
        await self._reply_html(update, format_unknown_command(txt or "/unknown"))

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not update.message or not update.message.text or not self._cb:
            return
        if not await self._check_allowed(update, notify=True):
            return
        msg = update.message
        _ = context
        self._remember_last_chat(update)
        self._record_message_to_ring(update)

        u = update.effective_user
        channel = str(msg.chat_id)
        sender_name = _telegram_display_name(u)
        text = self._inject_custom_emojis_into_text(msg)
        if not self._should_respond_to_group_message(msg, text):
            return

        # --- Summon continuation: respond to follow-ups without @mention ---
        is_summon_continuation = False
        if self._is_summon_active(channel):
            sender_id = str(u.id) if u else ""
            relevant = await self._check_summon_relevance(text, sender_id)
            if relevant:
                is_summon_continuation = True
                self._refresh_summon()
                if u:
                    self._active_summon["participants"].add(str(u.id))
                log.info(
                    "summon_continuation",
                    platform="telegram",
                    chat_id=msg.chat_id,
                    sender=sender_name,
                )

        log.info("telegram_inbound", platform="telegram", chat_id=msg.chat_id, kind="text")
        inp = Input(
            text=text,
            person_id=str(u.id) if u else "unknown",
            person_name=sender_name,
            channel=channel,
            platform="telegram",
            metadata={
                **self._telegram_msg_meta_with_session(msg),
                "summon_continuation": is_summon_continuation,
            },
        )
        await self._run_callback(inp)

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not update.message or not update.message.photo or not self._cb:
            return
        if not await self._check_allowed(update, notify=True):
            return
        msg = update.message
        caption = self._inject_custom_emojis_into_text(msg, raw_text=(msg.caption or "").strip())
        if not self._should_respond_to_group_message(msg, caption):
            return
        self._remember_last_chat(update)
        log.info("telegram_inbound", platform="telegram", chat_id=msg.chat_id, kind="photo")
        try:
            photo = msg.photo[-1]
            tfile = await context.bot.get_file(photo.file_id)
            data = await tfile.download_as_bytearray()
            b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
            caption = caption or "What do you see?"
            u = update.effective_user
            inp = Input(
                text=caption,
                person_id=str(u.id) if u else "unknown",
                person_name=_telegram_display_name(u),
                channel=str(msg.chat_id),
                platform="telegram",
                images=[{"base64": b64, "mime": "image/jpeg"}],
                metadata=self._telegram_msg_meta_with_session(msg),
            )
            await self._run_callback(inp)
        except Exception as e:
            log.warning("telegram_photo_failed", error=str(e))

    async def _on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not update.message or not self._cb:
            return
        if not await self._check_allowed(update, notify=True):
            return
        msg = update.message
        cap = self._inject_custom_emojis_into_text(msg, raw_text=(msg.caption or "").strip())
        if not self._should_respond_to_group_message(msg, cap):
            return
        self._remember_last_chat(update)
        log.info("telegram_inbound", platform="telegram", chat_id=msg.chat_id, kind="voice")
        file_id = None
        if msg.voice:
            file_id = msg.voice.file_id
        elif msg.audio:
            file_id = msg.audio.file_id
        if not file_id:
            return
        try:
            tfile = await context.bot.get_file(file_id)
            data = await tfile.download_as_bytearray()
            if len(data) > 8_000_000:
                return
            b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
            u = update.effective_user
            inp = Input(
                text=cap,
                person_id=str(u.id) if u else "unknown",
                person_name=_telegram_display_name(u),
                channel=str(msg.chat_id),
                platform="telegram",
                audio=[{"base64": b64, "format": "ogg"}],
                metadata=self._telegram_msg_meta_with_session(msg),
            )
            await self._run_callback(inp)
        except Exception as e:
            log.warning("telegram_voice_failed", error=str(e))

    async def _on_doc_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._take_update_if_fresh(update):
            return
        if not update.message or not update.message.document or not self._cb:
            return
        if not await self._check_allowed(update, notify=True):
            return
        doc = update.message.document
        mime = (doc.mime_type or "").lower()
        if not mime.startswith("image/"):
            return
        if not doc.file_id:
            return
        msg = update.message
        self._remember_last_chat(update)
        log.info("telegram_inbound", platform="telegram", chat_id=msg.chat_id, kind="document_image")
        try:
            tfile = await context.bot.get_file(doc.file_id)
            data = await tfile.download_as_bytearray()
            if len(data) > 8_000_000:
                return
            b64 = base64.standard_b64encode(bytes(data)).decode("ascii")
            raw_cap = (msg.caption or "").strip()
            cap = self._inject_custom_emojis_into_text(msg, raw_text=raw_cap) or "What's in this image?"
            u = update.effective_user
            inp = Input(
                text=cap,
                person_id=str(u.id) if u else "unknown",
                person_name=_telegram_display_name(u),
                channel=str(msg.chat_id),
                platform="telegram",
                images=[{"base64": b64, "mime": mime or "image/jpeg"}],
                metadata=self._telegram_msg_meta_with_session(msg),
            )
            await self._run_callback(inp)
        except Exception as e:
            log.warning("telegram_document_failed", error=str(e))

    async def connect(self) -> None:
        await self._refresh_privacy_from_db()
        await self._refresh_busy_prefs_from_db()
        await self.app.initialize()
        await self.app.start()
        try:
            await self.app.bot.set_my_name(self._bot_name)
            await self.app.bot.set_my_short_description(self._bot_short_description)
            await self.app.bot.set_my_description(self._bot_description)
            await self.app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except Exception as e:
            log.warning("telegram_set_profile_failed", error=str(e))
        try:
            excluded = self._menu_excluded_command_names(include_operator=False)
            menu = [
                BotCommand(command=name, description=desc)
                for name, desc in command_menu_items(excluded_names=excluded)
            ]
            await self.app.bot.set_my_commands(menu)
            for operator_id in sorted(self._operator_user_ids or set()):
                operator_menu = [
                    BotCommand(command=name, description=desc)
                    for name, desc in command_menu_items(
                        excluded_names=self._menu_excluded_command_names(include_operator=True)
                    )
                ]
                await self.app.bot.set_my_commands(
                    operator_menu,
                    scope=BotCommandScopeChat(chat_id=operator_id),
                )
        except Exception as e:
            log.warning("telegram_set_my_commands_failed", error=str(e))
        await self.app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            poll_interval=self._poll_interval,
            timeout=self._poll_timeout,
        )

    async def send_typing(self, chat_id: int) -> None:
        try:
            await self.app.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as e:
            log.debug("send_typing_failed", error=str(e))

    async def run_busy_indicator(
        self,
        chat_id: int,
        *,
        reply_to_message_id: int | None = None,
        stop: asyncio.Event,
    ) -> None:
        """Show an updating busy message; delete it when ``stop`` is set.

        Uses the raw Bot API (not ``send_message``) so group summon and the message ring stay untouched.
        """
        ing_word = random.choice(_BUSY_ING_WORDS)
        mid: int | None = None
        frame = 0
        send_kw: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _busy_indicator_html(0, ing_word),
            "parse_mode": "HTML",
            "disable_notification": True,
        }
        if reply_to_message_id is not None:
            send_kw["reply_to_message_id"] = reply_to_message_id
        try:
            sent = await self.app.bot.send_message(**send_kw)
            mid = int(sent.message_id)
        except Exception as e:
            log.debug("busy_indicator_send_failed", error=str(e))
            return

        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_BUSY_EDIT_INTERVAL_SEC)
                    break
                except asyncio.TimeoutError:
                    pass
                if stop.is_set():
                    break
                frame += 1
                if (
                    frame > 1
                    and (frame - 1) % _BUSY_WORD_ROTATE_EVERY == 0
                ):
                    ing_word = random.choice(_BUSY_ING_WORDS)
                try:
                    await self.app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=mid,
                        text=_busy_indicator_html(frame, ing_word),
                        parse_mode="HTML",
                    )
                except RetryAfter as e:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=float(e.retry_after) + 0.05)
                        break
                    except asyncio.TimeoutError:
                        pass
                    except (TypeError, ValueError):
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=1.0)
                            break
                        except asyncio.TimeoutError:
                            pass
                except TelegramError as e:
                    log.debug("busy_indicator_edit_failed", error=str(e))
                    break
        finally:
            if mid is not None:
                async def _cleanup() -> None:
                    try:
                        await self.app.bot.delete_message(chat_id=chat_id, message_id=mid)
                    except TelegramError as e:
                        log.debug("busy_indicator_delete_failed", error=str(e))
                # Delete the message in the background so run_busy_indicator can return immediately
                asyncio.create_task(_cleanup())

    async def send_attachment_bytes(
        self,
        channel: str,
        data: bytes,
        *,
        content_type: str | None = None,
        filename: str = "attachment.bin",
    ) -> None:
        """Deliver bytes from blob storage as photo (image/*) or document."""
        if not data:
            return
        chat_id = int(channel)
        ct = (content_type or "").lower()
        if ct.startswith("image/"):
            try:
                await self.app.bot.send_photo(chat_id=chat_id, photo=io.BytesIO(data))
                return
            except Exception as e:
                # Some images are rejected by Telegram's photo pipeline
                # (e.g. Image_process_failed). Fallback to document upload.
                log.warning("telegram_send_attachment_photo_failed", error=str(e))
        try:
            await self.app.bot.send_document(
                chat_id=chat_id,
                document=io.BytesIO(data),
                filename=filename[:255],
            )
        except Exception as e:
            log.warning("telegram_send_attachment_failed", error=str(e))
            raise RuntimeError(f"telegram attachment delivery failed: {e}") from e

    async def send_message(self, channel: str, content: str) -> None:
        chat_id = int(channel)
        text = content[:4096]
        await self.app.bot.send_message(chat_id=chat_id, text=text)
        if self._summon_enabled and chat_id < 0:
            self.open_summon(channel, "self")

    async def send_audio(self, channel: str, path: str) -> bool:
        p = Path(path)
        if not p.is_file():
            return False
        chat_id = int(channel)
        try:
            with p.open("rb") as f:
                # Prefer voice bubble for .ogg/.opus, fallback to generic audio.
                if p.suffix.lower() in (".ogg", ".opus"):
                    await self.app.bot.send_voice(chat_id=chat_id, voice=f)
                else:
                    await self.app.bot.send_audio(chat_id=chat_id, audio=f)
            return True
        except Exception as e:
            log.warning("telegram_send_audio_failed", error=str(e))
            return False

    async def send_image(self, channel: str, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            raise RuntimeError(f"image file not found: {path}")
        chat_id = int(channel)
        try:
            with p.open("rb") as f:
                await self.app.bot.send_photo(chat_id=chat_id, photo=f)
            return
        except Exception as e:
            # Fall back to document when Telegram cannot process as a photo.
            log.warning("telegram_send_image_photo_failed", error=str(e))
        try:
            with p.open("rb") as f:
                await self.app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=p.name[:255],
                )
        except Exception as e:
            log.warning("telegram_send_image_failed", error=str(e))
            raise RuntimeError(f"telegram image delivery failed: {e}") from e

    async def send_tool_activity(self, description: str) -> None:
        if not description.strip() or not self.last_chat_id:
            return
        esc = escape_markdown(description.strip(), version=2)
        text = f"_{esc}_"
        try:
            await self.app.bot.send_message(
                chat_id=int(self.last_chat_id),
                text=text[:4096],
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            log.debug("send_tool_activity_failed", error=str(e))

    async def send_plain_chunks(self, channel: str, text: str, pause: float = 1.0) -> None:
        """Plain chat: split into multiple Telegram messages under ``presence.message_chunk_max``.

        Previously this only split at ~4k (API limit), so normal replies were always a single
        bubble. Discord already splits by line budget; match that behavior using harness YAML.
        """
        t = text.strip()
        if not t:
            return
        try:
            h = self._entity.config.harness.presence
            max_len = max(120, min(3900, int(h.message_chunk_max)))
        except (AttributeError, TypeError, ValueError):
            max_len = 400
        parts: list[str] = []
        buf = ""
        for sentence in t.replace("\n\n", "\n").split("\n"):
            line = sentence.strip()
            if not line:
                continue
            if len(buf) + len(line) + 1 > max_len:
                if buf:
                    parts.append(buf)
                buf = line
            else:
                buf = (buf + " " + line).strip() if buf else line
        if buf:
            parts.append(buf)
        if len(parts) == 1 and len(parts[0]) > max_len:
            parts = [parts[0][i : i + max_len] for i in range(0, len(parts[0]), max_len)]
        out_parts = [p for p in parts if p.strip()]
        try:
            h = self._entity.config.harness.presence
            cps = max(8.0, float(h.typing_speed_base))
            var = max(0.0, min(0.85, float(h.typing_speed_variance)))
        except (AttributeError, TypeError, ValueError):
            cps = 30.0
            var = 0.3
        cid: int | None = None
        try:
            cid = int(str(channel).strip())
        except (TypeError, ValueError):
            cid = None
        for i, p in enumerate(out_parts):
            await self.send_message(channel, p)
            if i >= len(out_parts) - 1:
                break
            nxt = out_parts[i + 1]
            if cid is not None:
                try:
                    await self.send_typing(cid)
                except Exception:
                    pass
            # Human-paced gap: base pause with jitter + a fraction of "typing out" the next bubble.
            jitter = float(pause) * random.uniform(max(0.2, 1.0 - var), 1.0 + var)
            compose = min(6.0, max(0.15, len(nxt) / cps * 0.5))
            await asyncio.sleep(max(0.05, jitter + compose))

    async def send_proactive_default(self, message: str) -> None:
        if self.last_chat_id:
            await self.send_plain_chunks(self.last_chat_id, message)

    async def on_message(self, callback: Callable[..., Any]) -> None:
        self._cb = callback

    async def set_presence(self, status: str) -> None:
        pass

    async def disconnect(self) -> None:
        async with self._remote_sessions_lock:
            for session in self._remote_sessions.values():
                task = session.get("updater_task")
                if isinstance(task, asyncio.Task):
                    task.cancel()
        if self.app.updater is not None:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    def get_person_id(self, message: Any) -> str:
        if isinstance(message, Update):
            u = message.effective_user
            return str(u.id) if u else ""
        return ""

    def get_person_name(self, message: Any) -> str:
        if isinstance(message, Update):
            return _telegram_display_name(message.effective_user)
        return ""


def token_from_config(token_env: str) -> str:
    return os.environ.get(token_env, "")
