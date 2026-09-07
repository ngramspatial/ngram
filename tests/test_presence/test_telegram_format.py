from ngram.presence.automations.models import Automation, AutomationOrigin
from ngram.presence.platforms.telegram_format import (
    _relative_time,
    build_status_html,
    command_menu_items,
    format_body_md_reply_html_chunks,
    format_commands_page,
    format_help_html,
    format_journal_html_chunks,
    format_me_html,
    format_memories_html,
    format_remote_session_caption,
    format_routines_html,
    format_session_status_html,
    format_start_html,
    format_tools_html,
    format_version_html,
    format_wakequiet_status_html,
    split_telegram_chunks,
    telegram_registered_slash_command_names,
)


def test_relative_time_short_forms():
    now = __import__("time").time()
    assert _relative_time(None) == "—"
    assert _relative_time(now - 30) == "just now"
    assert _relative_time(now - 120).endswith("m ago")
    assert _relative_time(now - 7200).endswith("h ago")
    assert _relative_time(now - 3 * 86400).endswith("d ago")
    assert _relative_time(now - 14 * 86400).endswith("w ago")


def test_split_telegram_chunks_respects_limit():
    text = "Paragraph one.\n\n" + ("word " * 1200)
    chunks = split_telegram_chunks(text, limit=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    rebuilt = " ".join(c.strip() for c in chunks).strip()
    assert "Paragraph one." in rebuilt
    assert "word word" in rebuilt


def test_start_html_includes_personalized_name():
    out = format_start_html("Ari", "0.9.1", first_name="Maya")
    stale_domain = "ngram" + ".ai"
    assert "Maya" in out and "Ari" in out
    assert "/commands" in out
    assert "one continuous digital individual" in out
    assert "inner state keeps moving" in out
    assert "There doesn't have to be a task" in out
    assert "Ways to begin" in out and "Look inside" in out
    assert "0.9.1" not in out
    assert stale_domain not in out
    assert "entitative" not in out


def test_help_html_is_conversational_not_task_framed():
    out = format_help_html("Rook")
    assert "Talking with Rook" in out
    assert "let the conversation find its own direction" in out
    assert "Windows into my state" in out
    assert "15-minute plan" not in out


def test_version_html_shows_commit_not_italic_blurb():
    out = format_version_html(
        "1.0.0",
        commit_short="a1b2c3d",
        commit_subject="Ship /version with git subject",
    )
    assert "<b>ngram</b>" in out and "v1.0.0" in out
    assert "a1b2c3d" in out and "Ship /version" in out
    assert "<i>" not in out


def test_version_html_version_only_when_no_git():
    out = format_version_html("0.1.0")
    assert "v0.1.0" in out
    assert out.count("\n") == 0


def test_commands_page_supports_filter():
    out, page, total = format_commands_page(0, query="model")
    assert page == 0
    assert total >= 1
    assert "/models" in out


def test_body_md_reply_chunks_pre_and_escape():
    chunks = format_body_md_reply_html_chunks("# hi\n", chunk_chars=500)
    assert len(chunks) == 1
    assert "<pre>" in chunks[0] and "</pre>" in chunks[0]
    assert "# hi" in chunks[0]
    amp = format_body_md_reply_html_chunks("a & b < c", chunk_chars=500)
    assert "&amp;" in amp[0] and "&lt;" in amp[0]
    many = format_body_md_reply_html_chunks("x" * 6000, chunk_chars=100)
    assert len(many) >= 2


def test_journal_html_renders_recent_entries_newest_first():
    raw = """# journal

## 2026-08-29 08:00:00 #old

Old entry that should not be shown.

## 2026-08-30 09:15:00 #reflection #morning

I noticed <something> changing.

## 2026-09-01 21:04:00 #dream

### A quieter thought

- Stay with the uncertainty.
"""
    chunks = format_journal_html_chunks(raw, "Rook", requested_count=2)
    out = "\n".join(chunks)
    assert "Rook's journal" in out
    assert "Private reflections · newest first · showing 2 of 3 entries" in out
    assert "September 1, 2026 · 9:04 PM" in out
    assert out.index("September 1") < out.index("August 30")
    assert "<code>#dream</code>" in out
    assert "<b>A quieter thought</b>" in out
    assert "• Stay with the uncertainty." in out
    assert "&lt;something&gt;" in out
    assert "Old entry that should not be shown" not in out


def test_journal_html_has_empty_state_and_safe_chunking():
    empty = format_journal_html_chunks("# journal\n", "Rook")
    assert len(empty) == 1
    assert "No journal entries yet" in empty[0]

    raw = "# journal\n\n## 2026-09-01 21:04:00 #long\n\n" + ("<thought> " * 1000)
    chunks = format_journal_html_chunks(raw, "Rook", requested_count=1, chunk_chars=1200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert all("<thought>" not in chunk for chunk in chunks)
    assert "journal · continued" in chunks[1]


def test_format_wakequiet_status_html_modes():
    quiet = format_wakequiet_status_html(
        quiet_db=True, yaml_status_mirror=True, yaml_tools_mirror=False,
    )
    assert "Wake quiet mode: on" in quiet and "transcript" in quiet
    normal = format_wakequiet_status_html(
        quiet_db=False, yaml_status_mirror=True, yaml_tools_mirror=False,
    )
    assert "Wake quiet mode: off" in normal and "wake_user_visible_status" in normal


def test_command_menu_items_are_short():
    items = command_menu_items()
    assert items
    assert any(name == "start" for name, _ in items)
    assert any(name == "tools" for name, _ in items)
    assert any(name == "routines" for name, _ in items)
    assert any(name == "session_start" for name, _ in items)
    assert any(name == "session_status" for name, _ in items)
    assert any(name == "session_stop" for name, _ in items)
    assert any(name == "compact" for name, _ in items)
    assert any(name == "busy" for name, _ in items)
    assert any(name == "wakequiet" for name, _ in items)
    assert any(name == "body" for name, _ in items)
    assert any(name == "journal" for name, _ in items)
    assert all(len(desc) <= 256 for _, desc in items)


def test_command_menu_items_can_hide_disabled_capabilities():
    excluded = {
        "private",
        "privacy",
        "routines",
        "session_start",
        "session_status",
        "session_stop",
        "update",
        "wakequiet",
    }
    names = {name for name, _ in command_menu_items(excluded_names=excluded)}
    assert not names.intersection(excluded)
    assert {"start", "commands", "status", "body", "tools"}.issubset(names)


def test_commands_page_hides_disabled_capabilities():
    body, _, _ = format_commands_page(
        0,
        per_page=100,
        excluded_names={"session_start", "session_status", "session_stop"},
    )
    assert "/session_start" not in body
    assert "/session_status" not in body
    assert "/session_stop" not in body


def test_telegram_registered_slash_names_include_aliases():
    names = telegram_registered_slash_command_names()
    assert "about" in names
    assert "privacy" in names
    assert "private" in names
    assert "whoami" in names
    assert "session_start" in names
    assert "session_status" in names
    assert "session_stop" in names
    assert "compact" in names
    assert "busy" in names
    assert "wakequiet" in names
    assert "body" in names
    assert "journal" in names
    assert "memeories" not in names
    for name, _ in command_menu_items():
        assert name in names


def test_format_memories_html_renders_episode_like_rows():
    class _Ep:
        summary = "Discussed migration strategy."
        timestamp = __import__("time").time() - 120
        significance = 0.73

    out = format_memories_html("ngram", [_Ep()], requested_count=5)
    assert "Showing 1 of 5 requested" in out
    assert "Discussed migration strategy." in out
    assert "sig 0.73" in out
    assert "#1" in out
    assert "episode" in out


def test_format_memories_html_renders_real_memory_dict_rows():
    out = format_memories_html(
        "ngram",
        [
            {
                "kind": "procedural",
                "title": "deploy-playbook",
                "body": "Always checkpoint before rollout.",
                "timestamp": __import__("time").time() - 30,
            },
            {
                "kind": "belief",
                "title": "preference",
                "body": "User prefers concise updates.",
                "timestamp": __import__("time").time() - 90,
            },
        ],
        requested_count=2,
    )
    assert "procedural" in out
    assert "belief" in out
    assert "deploy-playbook" in out
    assert "User prefers concise updates." in out


def test_format_memories_html_empty_state_guides_user():
    out = format_memories_html("ngram", [], requested_count=5)
    assert "No episodic memories yet" in out
    assert "/memories 5" in out


def test_format_me_html_includes_target_and_traces():
    class _Rel:
        name = "Maya"
        dynamic = "warming"
        familiarity = 0.44
        warmth = 0.52
        trust = 0.61
        interaction_count = 7
        first_met = __import__("time").time() - 86_400
        last_interaction = __import__("time").time() - 60

    out = format_me_html("ngram", _Rel(), target_label="Maya")
    assert "Relationship · Maya" in out
    assert "Recent shared memory traces" not in out


def test_format_memories_html_uses_visual_separators_between_items():
    class _Ep:
        def __init__(self, summary: str, ts: float, sig: float):
            self.summary = summary
            self.timestamp = ts
            self.significance = sig

    now = __import__("time").time()
    out = format_memories_html(
        "ngram",
        [
            _Ep("First memory block.", now - 30, 0.20),
            _Ep("Second memory block.", now - 120, 0.40),
        ],
        requested_count=2,
    )
    assert "────────────────────" in out
    assert "#1" in out and "#2" in out


def test_routines_html_lists_automations():
    a = Automation(
        name="Morning check-in",
        description="Brief scan",
        schedule_natural="every day at 9am",
        cron_expression="0 9 * * *",
        origin=AutomationOrigin.SELF,
        enabled=True,
        run_count=2,
        last_result_summary="done",
        deliver_to="telegram:1",
        last_run=__import__("time").time() - 7200,
    )
    out = format_routines_html(
        "ngram",
        [a],
        automations_enabled=True,
        scheduler_ready=True,
    )
    assert "ngram's Routines" in out
    assert "1 active · 0 paused" in out
    assert "Morning check-in" in out
    assert "every day at 9am" in out
    assert "→ Telegram" in out
    assert "Ran 2 times" in out
    assert "2h ago" in out
    assert '"Brief scan"' in out
    assert "0 9 * * *" not in out


def test_routines_html_empty_and_notes():
    out = format_routines_html(
        "ngram",
        [],
        automations_enabled=False,
        scheduler_ready=False,
    )
    assert "No routines yet" in out
    assert "scheduling" in out and "disabled" in out
    assert "daemon is running" in out


def test_routines_html_internal_skips_description():
    a = Automation(
        name="Nightly reflection",
        description="Write in journal",
        schedule_natural="every night at 11pm",
        cron_expression="0 23 * * *",
        origin=AutomationOrigin.INTERNAL,
        enabled=True,
        run_count=1,
        last_run=__import__("time").time() - 3600,
    )
    out = format_routines_html("Canary", [a], automations_enabled=True, scheduler_ready=True)
    assert "📓" in out
    assert "Internal" in out
    assert "Write in journal" not in out


def test_routines_html_sorts_active_before_paused():
    newer = Automation(
        name="A",
        schedule_natural="daily",
        origin=AutomationOrigin.USER,
        enabled=True,
        next_run=200.0,
        run_count=0,
    )
    older = Automation(
        name="B",
        schedule_natural="daily",
        origin=AutomationOrigin.USER,
        enabled=True,
        next_run=100.0,
        run_count=0,
    )
    paused = Automation(
        name="Z",
        schedule_natural="daily",
        origin=AutomationOrigin.USER,
        enabled=False,
        next_run=50.0,
        run_count=0,
    )
    out = format_routines_html("E", [newer, older, paused], automations_enabled=True, scheduler_ready=True)
    pos_b = out.find("<b>⏰  B</b>")
    pos_a = out.find("<b>⏰  A</b>")
    pos_z = out.find("<b>⏸  Z</b>")
    assert pos_b < pos_a < pos_z


def test_tools_html_lists_registered_tools():
    class _Tools:
        def list_tools(self):
            return [
                ("search_web", "Search the web"),
                ("get_current_time", "Get current date and time"),
            ]

    class _Entity:
        tools = _Tools()

    out, page, total = format_tools_html(_Entity())
    assert "Active tools" in out
    assert "search_web" in out
    assert "get_current_time" in out
    assert page == 0
    assert total == 1


def test_session_status_html_handles_empty_and_active():
    empty = format_session_status_html("ngram", None)
    assert "No active desktop session" in empty
    active = format_session_status_html(
        "ngram",
        {
            "session_id": "sess_123",
            "status": "running",
            "active_app": "Firefox",
            "last_action": "opened docs",
            "summary": "Watching the browser window.",
        },
    )
    assert "sess_123" in active
    assert "Firefox" in active
    assert "opened docs" in active


def test_remote_session_caption_stays_compact():
    caption = format_remote_session_caption(
        "ngram",
        {
            "status": "running",
            "active_app": "Firefox",
            "last_action": "opened docs",
            "summary": "Watching the browser window.",
        },
    )
    assert "ngram remote session" in caption
    assert "Firefox" in caption
    assert len(caption) <= 1024


def test_build_status_html_reports_soma_gen_snapshot():
    class _SessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Store:
        def session(self):
            return _SessionCtx()

        async def count_episodes(self, _db):
            return 12

        async def count_relationships(self, _db):
            return 4

        async def min_episode_timestamp(self, _db):
            return __import__("time").time() - 3600

    class _Emotions:
        class _State:
            class _Primary:
                value = "curious"

            primary = _Primary()

        def get_state(self):
            return self._State()

    class _Drives:
        def all_drives(self):
            return []

    class _Tools:
        def openai_tools(self):
            return [{"function": {"name": "search_web"}}]

    class _Bars:
        ordered_names = ["social", "curiosity"]
        _active_conflicts = [{"label": "restless comfort"}]
        _active_impulses = [{"label": "reach_out", "on_cooldown": False}]

        def snapshot_pct(self):
            return {"social": 72, "curiosity": 65}

    class _Noise:
        cycle_seconds = 90.0
        temperature = 1.1
        max_tokens = 150

        def current_fragments(self):
            return ["thinking about that deploy note"]

    class _Cog:
        reflex_model = "gemma3:4b"
        max_context_tokens = 32000
        rolling_history_max_messages = 40

        class _HistoryCompression:
            enabled = True
            compaction_threshold_ratio = 0.6
            compaction_target_ratio = 0.08

        history_compression = _HistoryCompression()

    class _Harness:
        class _Deployment:
            mode = "local"

        class _Autonomy:
            enabled = True
            impulse_wake = True
            drive_wake = True
            conflict_wake = True
            noise_wake = False
            desire_wake = True
            desire_wake_threshold = 0.72
            max_desires_considered = 3
            allow_tool_calls_on_wake = True

        class _Memory:
            class _Distillation:
                enabled = True
                cycle_seconds = 300.0

            distillation = _Distillation()

        deployment = _Deployment()
        autonomy = _Autonomy()
        memory = _Memory()

    class _Cfg:
        name = "ngram"
        cognition = _Cog()
        harness = _Harness()
        raw = {"created": "2026-04-09T00:00:00Z"}

        def soma_dir(self):
            return __import__("tempfile").gettempdir() + "/ngram-test-soma-status"

    class _Tonic:
        bars = _Bars()
        noise = _Noise()
        _noise_enabled = True
        _noise_model = ""
        _current_affects = [{"name": "fascination", "intensity": 0.7}]
        _appraisal_enabled = True

        def render_body(self):
            return (
                "## Bars\n"
                "social     █████░░░░░  moderate  —\n"
                "curiosity  █████░░░░░  moderate  —\n\n"
                "## Affects\n(flat — body not naming a texture yet)\n"
            )

    class _Entity:
        store = _Store()
        config = _Cfg()
        emotions = _Emotions()
        drives = _Drives()
        tools = _Tools()
        tonic = _Tonic()

        @staticmethod
        def list_known_person_routes(_platform):
            return ["123"]

    out = __import__("asyncio").run(build_status_html(_Entity(), "1.2.3"))
    assert "Architecture" in out
    assert "SOMA" in out
    assert "GEN" in out
    assert "Autonomy &amp; wake" in out
    assert "Cognition" in out
    assert "Memory pipeline" in out
    assert "Daemon / automations" in out
    assert "Dominant bar — social 72%" in out
    assert "GEN model <code>gemma3:4b</code>" in out
    assert "Bars (body.md)" in out
    assert "Deployment <code>local</code>" in out
    assert "moderate" in out
