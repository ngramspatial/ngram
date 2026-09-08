"""Primary agent loop with tool use + bounded continuation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

import structlog

from ngram.config import EntityConfig
from ngram.cognition import gemma
from ngram.cognition.context_status import report_context_status
from ngram.cognition.history_compression import (
    estimate_context_tokens,
    find_compaction_boundaries,
    generate_structured_summary,
    prune_old_tool_results,
    sanitize_tool_pairs,
)
from ngram.models import Input
from ngram.inference.protocol import InferenceProvider
from ngram.inference.types import ChatCompletionResult, ToolCallSpec
from ngram.inference.visual_results import expire_visuals, text_history
from ngram.presence.tools.registry import (
    TOOL_SYSTEM_PROMPT_PREFIX,
    TOOL_SYSTEM_PROMPT_PREFIX_COMPACT,
)

log = structlog.get_logger("ngram.cognition.deliberate")

InferenceProfile = Literal["deliberate", "reflex"]


def fit_system_prompt_to_budget(system_prompt: str, cap: int) -> str:
    """
    Apply ``cap`` without dropping the trailing tools appendix: the naive ``[:cap]`` cut
    removes everything after the identity/knowledge prefix, so the model never sees tool cues.
    """
    s = system_prompt or ""
    if len(s) <= cap:
        return s
    suffix_start = -1
    for marker in (TOOL_SYSTEM_PROMPT_PREFIX, TOOL_SYSTEM_PROMPT_PREFIX_COMPACT):
        idx = s.find(marker)
        if idx != -1 and (suffix_start == -1 or idx < suffix_start):
            suffix_start = idx
    if suffix_start == -1:
        return s[:cap]
    suffix = s[suffix_start:]
    if len(suffix) >= cap:
        return s[:cap]
    room = cap - len(suffix)
    return s[:suffix_start][:room] + suffix


_TOOL_CONTINUATION_USER = (
    "Same turn. Keep going until you can either answer the person clearly from the current tool "
    "results, call another tool, or state the exact failure. Do not stop at progress chatter."
)

_RESEARCH_TOOLS = frozenset({
    "search_web", "fetch_url", "read_wikipedia", "get_news",
    "search_youtube", "read_reddit", "read_reddit_post",
})

# A large message budget is useful while real work is happening, but some models
# can get trapped paraphrasing a completed result through say(). Concrete tool
# calls reset this guard; only consecutive speech-only rounds count toward it.
_COMMUNICATION_ONLY_TOOLS = frozenset({"say", "think", "wait", "ar_speak"})
_MAX_CONSECUTIVE_COMMUNICATION_ONLY_ROUNDS = 3


def _build_post_tool_nudge(
    tool_calls: list[ToolCallSpec],
    tool_msgs: list[dict[str, Any]],
    user_text: str,
    *,
    already_sent: list[str] | None = None,
) -> str:
    """Short nudge after tool results — trust the model to decide what comes next."""
    parts: list[str] = ["Same turn. Tool results above."]

    # Flag failures so the model knows what went wrong without re-reading.
    failed = [
        tc.name for tc, tm in zip(tool_calls, tool_msgs)
        if _tool_content_failed(tm.get("content") or "")
    ]
    if failed:
        parts.append(f"Failed: {', '.join(failed)}.")

    # Remind what the user already heard — only anti-repetition, no prescriptive flow.
    if already_sent:
        snippets = "; ".join(m[:80] for m in already_sent[-3:])
        parts.append(f"Already told user: {snippets}")

    speech_only = bool(tool_calls) and all(
        tc.name in _COMMUNICATION_ONLY_TOOLS for tc in tool_calls
    )
    if speech_only and already_sent:
        parts.append(
            "Do not paraphrase what you already said. Next call end_turn if the work is done, "
            "or call a concrete non-communication tool if specific work remains."
        )
    else:
        parts.append(
            "Continue: use say() to share findings, call more tools if needed, "
            "or end_turn when done."
        )
    return " ".join(parts)

_FINAL_RECOVERY_USER = "Same turn. Keep going — answer the user or state what failed."


def _finish_reason_hits_limit(finish_reason: str | None) -> bool:
    if not finish_reason:
        return False
    fr = finish_reason.lower().replace("_", "").replace("-", "")
    return fr in ("length", "maxtokens", "maxlength")


def _tool_content_failed(content: str) -> bool:
    raw = (content or "").strip()
    if not raw:
        return False
    lt = raw.lower()
    if "not installed" in lt or ("missing" in lt and "package" in lt):
        return True
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return '"error"' in raw
    if isinstance(obj, dict):
        if obj.get("error"):
            return True
        if obj.get("ok") is False:
            return True
    return False


def _visible_assistant_text(res: ChatCompletionResult) -> str:
    return (res.content or "").strip()


@dataclass
class AgentLoopState:
    step_index: int = 0
    step_budget: int = 0
    tool_rounds: int = 0
    tool_calls_seen: int = 0
    communication_only_rounds: int = 0
    last_step_had_tools: bool = False
    last_tool_failed: bool = False
    completion_failures: int = 0
    user_requested_tools: bool = False


FinalCheck = Callable[[str, ChatCompletionResult, AgentLoopState], Awaitable[tuple[bool, str | None]]]


@dataclass
class DeliberateStreamEvent:
    """One slice of a multi-round deliberate run (tool loop)."""

    kind: Literal["intermediate", "final"]
    display_text: str = ""
    """Visible user-directed text from the model for this slice (intermediate or final)."""
    history_entries: list[dict[str, Any]] = field(default_factory=list)
    """Assistant + tool + follow-up messages to merge into rolling history (intermediate only)."""
    merged_thinking: str | None = None
    """CoT / reasoning accumulated across all model calls in this perceive cycle (final only)."""
    last_result: ChatCompletionResult | None = None
    """Raw completion for the final model call (truncation extension, metadata)."""


class DeliberateCognition:
    def __init__(self, entity: EntityConfig, client: InferenceProvider) -> None:
        self.entity = entity
        self.latest_context_status: dict[str, Any] | None = None
        self.client = client

    def _inference_params(self, profile: InferenceProfile) -> tuple[str, int | None, bool, float]:
        """Model id, max_tokens, think flag, temperature."""
        h = self.entity.harness.cognition
        ec = self.entity.cognition
        if profile == "reflex":
            model = (ec.reflex_model or "").strip()
            if not model:
                model = (ec.deliberate_model or "").strip()
            mt = max(32, int(h.reflex_max_tokens))
            return model, mt, False, float(min(0.9, ec.temperature))
        model = (ec.deliberate_model or "").strip()
        output_cap = (
            None if ec.deliberate_max_tokens is None
            else max(128, int(ec.deliberate_max_tokens or h.deliberate_max_tokens))
        )
        return model, output_cap, bool(ec.thinking_mode), float(ec.temperature)

    def _tool_continuation_rounds_cap(self) -> int:
        """Legacy continuation knob; still shapes the bounded agent loop."""
        h = int(self.entity.harness.cognition.tool_continuation_rounds)
        ec = int(self.entity.cognition.tool_continuation_rounds)
        n = ec if ec > 0 else h
        return max(0, n)

    def _agent_step_cap(self) -> int:
        """Primary bounded loop budget for a single user turn."""
        return max(6, 6 + self._tool_continuation_rounds_cap())

    def _build_messages(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        cap = max(2000, int(self.entity.cognition.system_prompt_char_limit or 12000))
        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": fit_system_prompt_to_budget(system_prompt, cap)},
        ]
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                # Preserve multimodal user/tool payloads (e.g. image_url) for OpenAI-compatible backends.
                # Only assistant content should be flattened to text for history carryover.
                if role == "assistant":
                    content = gemma.stringify_content_blocks(content)
            elif role == "assistant" and isinstance(content, str):
                content = gemma.strip_thinking_for_history(content)
            entry: dict[str, Any] = {"role": role, "content": content}
            if m.get("tool_calls"):
                entry["tool_calls"] = m["tool_calls"]
            if role == "tool":
                entry["tool_call_id"] = m.get("tool_call_id", "")
                entry["name"] = m.get("name", "")
            msgs.append(entry)
        return msgs

    async def _compact_active_turn_if_needed(
        self,
        messages: list[dict[str, Any]],
        *,
        initial_message_count: int,
        previous_summary: str,
        tools_block: str = "",
    ) -> tuple[list[dict[str, Any]], str, bool]:
        """Compact an in-flight tool trace without losing the original directive.

        Conversation history is compacted before the loop. Long directives can
        then add dozens of tool/result messages, so they need a second boundary:
        preserve the entire initial prompt, summarize completed work, and retain
        a verbatim tail containing the latest tool group and next decision point.
        """
        hc = self.entity.cognition.history_compression
        if not hc.enabled:
            return messages, previous_summary, False
        context_length = self.entity.effective_context_tokens()
        threshold = int(context_length * float(hc.compaction_threshold_ratio))
        if estimate_context_tokens("", messages, tools_block=tools_block) < threshold:
            return messages, previous_summary, False

        initial = messages[:initial_message_count]
        active = messages[initial_message_count:]
        if len(active) < 8:
            pruned, count = prune_old_tool_results(messages, protect_tail_count=6)
            return pruned, previous_summary, bool(count)

        _, compress_end = find_compaction_boundaries(
            active,
            head_n=0,
            tail_token_budget=max(
                512,
                int(context_length * float(hc.compaction_target_ratio)),
            ),
            min_tail_n=6,
        )
        completed = active[:compress_end]
        if not completed:
            pruned, count = prune_old_tool_results(messages, protect_tail_count=6)
            return pruned, previous_summary, bool(count)

        await report_context_status("compacting", source="active_turn", automatic=True,
                                    inputBudgetTokens=context_length)
        summary = await generate_structured_summary(
            self.client,
            self.entity.cognition.reflex_model or self.entity.cognition.deliberate_model,
            turns=completed,
            previous_summary=previous_summary,
            entity_name=self.entity.name,
            context_length=context_length,
            num_ctx=self.entity.effective_ollama_num_ctx(),
        )
        if not summary:
            await report_context_status("failed", source="active_turn", automatic=True,
                                        inputBudgetTokens=context_length)
            return messages, previous_summary, False

        continuation = {
            "role": "user",
            "content": (
                summary
                + "\n\n[ACTIVE DIRECTIVE CONTINUES]\n"
                "The original user directive remains above. Continue from this working-state summary "
                "and the verbatim recent tool trace below. Do not restart, claim completion, or ask the "
                "user to repeat context merely because compaction occurred."
            ),
        }
        tail = sanitize_tool_pairs(active[compress_end:])
        rebuilt = initial + [continuation] + tail
        await report_context_status(
            "compacted", source="active_turn", automatic=True,
            inputBudgetTokens=context_length,
            estimatedTokens=estimate_context_tokens("", rebuilt, tools_block=tools_block),
            messagesBefore=len(messages), messagesAfter=len(rebuilt),
        )
        log.info(
            "active_turn_compacted",
            completed_messages=len(completed),
            tail_messages=len(tail),
            before_messages=len(messages),
            after_messages=len(rebuilt),
        )
        return rebuilt, summary, True

    @staticmethod
    def _assistant_content_for_message(res: ChatCompletionResult) -> str:
        return (
            res.raw_assistant_text
            if (res.raw_assistant_text or "").strip()
            else (res.content or "")
        )

    async def iter_responses(
        self,
        _inp: Input,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[ToolCallSpec], Awaitable[str]] | None = None,
        inference_profile: InferenceProfile = "deliberate",
        final_checker: FinalCheck | None = None,
    ) -> AsyncIterator[DeliberateStreamEvent]:
        """Primary bounded agent loop for one user turn."""
        model, max_toks, think_flag, temperature = self._inference_params(inference_profile)
        msgs = self._build_messages(system_prompt, messages)
        initial_message_count = len(msgs)
        active_turn_summary = ""
        tools_budget_block = (
            json.dumps(tools, ensure_ascii=False, separators=(",", ":")) if tools else ""
        )
        thinking_acc: list[str] = []
        last: ChatCompletionResult | None = None
        num_ctx = self.entity.effective_ollama_num_ctx()
        step_cap = self._agent_step_cap()
        meta = getattr(_inp, "metadata", None) or {}
        if isinstance(meta, dict):
            if meta.get("delegation"):
                cap = int(meta.get("delegation_max_steps") or 10)
                step_cap = max(3, min(max(25, self._agent_step_cap()), cap))
            elif meta.get("code_task"):
                cap = int(meta.get("code_task_max_steps") or 14)
                step_cap = max(5, min(max(25, self._agent_step_cap()), cap))
            ss = meta.get("sustained_session")
            if isinstance(ss, dict):
                extra = int(ss.get("extra_tool_steps", 0) or 0)
                if extra > 0:
                    step_cap = max(step_cap, min(40, step_cap + extra))
        loop_state = AgentLoopState(
            step_budget=step_cap,
            user_requested_tools="use tools" in (_inp.text or "").lower(),
        )
        initial_budget = loop_state.step_budget
        # The configured budget is a ceiling, including recovery/judge rounds.
        # Extending it after every rejected completion can silently spend hundreds
        # of model calls on a turn the model has already tried to finish.
        hard_step_cap = initial_budget
        _messages_sent_to_user: list[str] = []
        delivered_reply = False
        step_idx = 0
        while step_idx < loop_state.step_budget and step_idx < hard_step_cap:
            from ngram.inference.factory import effective_inference_provider_name
            self.latest_context_status = {
                "phase": "usage", "source": "prompt", "model": model,
                "provider": effective_inference_provider_name(self.entity.harness),
                "inputBudgetTokens": self.entity.effective_context_tokens(),
                "estimatedTokens": estimate_context_tokens("", msgs, tools_block=tools_budget_block),
            }
            await report_context_status(**self.latest_context_status)
            loop_state.step_index = step_idx + 1
            res = await self.client.chat_completion(
                model,
                msgs,
                tools=tools,
                tool_choice="required" if tools else None,
                temperature=temperature,
                max_tokens=max_toks,
                think=think_flag,
                num_ctx=num_ctx,
            )
            last = res
            if res.thinking:
                thinking_acc.append(res.thinking)

            if tool_executor and res.tool_calls:
                for tc in res.tool_calls:
                    if not tc.id:
                        tc.id = gemma.new_tool_call_id()

                loop_state.last_step_had_tools = True
                loop_state.tool_rounds += 1
                loop_state.tool_calls_seen += len(res.tool_calls)
                assistant_content = self._assistant_content_for_message(res)
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in res.tool_calls
                    ],
                }
                tool_msgs: list[dict[str, Any]] = []
                tool_failed = False
                for tc in res.tool_calls:
                    try:
                        out = await tool_executor(tc)
                    except Exception as e:
                        out = json.dumps({"error": str(e)})
                    tool_failed = tool_failed or _tool_content_failed(out)
                    tool_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": out,
                        }
                    )
                loop_state.last_tool_failed = tool_failed

                delivered_reply = delivered_reply or any(
                    tc.name in {"say", "ar_speak"}
                    and (
                        str(tm.get("content", "")).startswith("[sent]")
                        or str(tm.get("content", "")).startswith("[spatial: speak queued]")
                    )
                    for tc, tm in zip(res.tool_calls, tool_msgs)
                )
                end_turn_requested = any(
                    tc.name in {"end_turn", "end_wake_session"}
                    and "[turn ended]" in str(tm.get("content", ""))
                    for tc, tm in zip(res.tool_calls, tool_msgs)
                )
                if end_turn_requested:
                    done = True
                    recovery = None
                    # A message that was already delivered cannot be taken back. If the
                    # model explicitly ends the turn after delivering its reply, continuing
                    # through a completion gate only risks sending the same response again
                    # whether say() and end_turn() occur together or in separate rounds.
                    if final_checker is not None and not delivered_reply:
                        done, recovery = await final_checker(
                            assistant_content.strip(),
                            res,
                            loop_state,
                        )
                    if done:
                        merged = "\n\n".join(thinking_acc) if thinking_acc else None
                        yield DeliberateStreamEvent(
                            kind="final",
                            display_text=assistant_content.strip(),
                            history_entries=[],
                            merged_thinking=merged,
                            last_result=res,
                        )
                        return
                    loop_state.completion_failures += 1
                    follow_user = {
                        "role": "user",
                        "content": recovery or _FINAL_RECOVERY_USER,
                    }
                    msgs = expire_visuals(msgs + [assistant_msg] + tool_msgs + [follow_user])
                    msgs, active_turn_summary, _ = await self._compact_active_turn_if_needed(
                        msgs,
                        initial_message_count=initial_message_count,
                        previous_summary=active_turn_summary,
                        tools_block=tools_budget_block,
                    )
                    yield DeliberateStreamEvent(
                        kind="intermediate",
                        display_text="",
                        history_entries=text_history([assistant_msg] + tool_msgs + [follow_user]),
                    )
                    step_idx += 1
                    continue

                for tc, tm in zip(res.tool_calls, tool_msgs):
                    if tc.name == "say" and str(tm.get("content", "")).startswith("[sent]"):
                        msg = tc.arguments.get("message", "")
                        if msg:
                            _messages_sent_to_user.append(str(msg)[:200])
                vis = _visible_assistant_text(res)
                if vis and vis not in ("[thought recorded]", "[turn ended]"):
                    _messages_sent_to_user.append(vis[:200])

                delivered_say = any(
                    tc.name in {"say", "ar_speak"}
                    and (
                        str(tm.get("content", "")).startswith("[sent]")
                        or "queued" in str(tm.get("content", ""))
                    )
                    for tc, tm in zip(res.tool_calls, tool_msgs)
                )
                communication_only = all(
                    tc.name in _COMMUNICATION_ONLY_TOOLS for tc in res.tool_calls
                )
                if communication_only and delivered_say:
                    loop_state.communication_only_rounds += 1
                elif not communication_only:
                    loop_state.communication_only_rounds = 0

                if (
                    delivered_say
                    and loop_state.communication_only_rounds
                    >= _MAX_CONSECUTIVE_COMMUNICATION_ONLY_ROUNDS
                ):
                    log.warning(
                        "deliberate_loop_force_done_repetitive_speech",
                        speech_only_rounds=loop_state.communication_only_rounds,
                        step=loop_state.step_index,
                    )
                    yield DeliberateStreamEvent(
                        kind="intermediate",
                        display_text="",
                        history_entries=text_history([assistant_msg] + tool_msgs),
                    )
                    merged = "\n\n".join(thinking_acc) if thinking_acc else None
                    yield DeliberateStreamEvent(
                        kind="final",
                        display_text="",
                        history_entries=[],
                        merged_thinking=merged,
                        last_result=res,
                    )
                    return

                follow_user = {
                    "role": "user",
                    "content": _build_post_tool_nudge(
                        res.tool_calls, tool_msgs, _inp.text,
                        already_sent=_messages_sent_to_user if _messages_sent_to_user else None,
                    ),
                }
                msgs = expire_visuals(msgs + [assistant_msg] + tool_msgs + [follow_user])
                msgs, active_turn_summary, _ = await self._compact_active_turn_if_needed(
                    msgs,
                    initial_message_count=initial_message_count,
                    previous_summary=active_turn_summary,
                    tools_block=tools_budget_block,
                )

                yield DeliberateStreamEvent(
                    kind="intermediate",
                    display_text="",
                    history_entries=text_history([assistant_msg] + tool_msgs + [follow_user]),
                )
                step_idx += 1
                continue

            loop_state.last_step_had_tools = False
            visible = _visible_assistant_text(res)
            done = True
            recovery = None
            if _finish_reason_hits_limit(res.finish_reason):
                loop_state._consecutive_length = getattr(loop_state, "_consecutive_length", 0) + 1
            else:
                loop_state._consecutive_length = 0
            if loop_state._consecutive_length >= 3:
                # Never append write_file guidance to ``visible`` — that string becomes
                # ``display_text`` and can leak to Telegram/Discord. The model still gets
                # truncation recovery via ``recovery`` / the gate when the loop continues.
                if final_checker is not None:
                    done, recovery = await final_checker(visible, res, loop_state)
                else:
                    done = False
                    recovery = (
                        "Same turn. Your output was cut off by the token limit. "
                        "If you're writing code or long content, use write_file to save it to disk "
                        "instead of outputting it as text. Then tell the user what you wrote."
                    )
            elif final_checker is not None:
                done, recovery = await final_checker(visible, res, loop_state)
            elif _finish_reason_hits_limit(res.finish_reason):
                done = False
                recovery = (
                    "Same turn. Your output was cut off by the token limit. "
                    "If you're writing code or long content, use write_file to save it to disk "
                    "instead of outputting it as text. Then tell the user what you wrote."
                )
            if not done:
                loop_state.completion_failures += 1
                asst = self._assistant_content_for_message(res)
                asst_msg = {"role": "assistant", "content": asst}
                cont_user = {"role": "user", "content": recovery or _FINAL_RECOVERY_USER}
                msgs = msgs + [asst_msg, cont_user]
                msgs, active_turn_summary, _ = await self._compact_active_turn_if_needed(
                    msgs,
                    initial_message_count=initial_message_count,
                    previous_summary=active_turn_summary,
                    tools_block=tools_budget_block,
                )
                log.info(
                    "deliberate_loop_continue",
                    step=loop_state.step_index,
                    step_budget=loop_state.step_budget,
                    completion_failures=loop_state.completion_failures,
                    finish_reason=res.finish_reason,
                )
                yield DeliberateStreamEvent(
                    kind="intermediate",
                    display_text="",
                    history_entries=[asst_msg, cont_user],
                )
                step_idx += 1
                continue

            merged = "\n\n".join(thinking_acc) if thinking_acc else None
            yield DeliberateStreamEvent(
                kind="final",
                display_text=visible,
                history_entries=[],
                merged_thinking=merged,
                last_result=res,
            )
            return

        merged = "\n\n".join(thinking_acc) if thinking_acc else None
        log.info(
            "deliberate_loop_budget_exhausted",
            step_budget=loop_state.step_budget,
            hard_cap=hard_step_cap,
            completion_failures=loop_state.completion_failures,
        )
        yield DeliberateStreamEvent(
            kind="final",
            display_text=(last.content or "").strip() if last else "",
            history_entries=[],
            merged_thinking=merged,
            last_result=last or ChatCompletionResult(content=""),
        )

    async def respond(
        self,
        _inp: Input,
        system_prompt: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[ToolCallSpec], Awaitable[str]] | None = None,
        inference_profile: InferenceProfile = "deliberate",
        final_checker: FinalCheck | None = None,
    ) -> ChatCompletionResult:
        """Aggregate a full deliberate run (single return value)."""
        final_ev: DeliberateStreamEvent | None = None
        async for ev in self.iter_responses(
            _inp,
            system_prompt,
            messages,
            tools=tools,
            tool_executor=tool_executor,
            inference_profile=inference_profile,
            final_checker=final_checker,
        ):
            if ev.kind == "final":
                final_ev = ev
        if not final_ev or not final_ev.last_result:
            return ChatCompletionResult(content="")
        r = final_ev.last_result
        return replace(r, thinking=final_ev.merged_thinking)
