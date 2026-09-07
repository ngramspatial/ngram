"""First-person system prompt via deliberate model + conservative cache."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ngram.models import EmotionalState
from ngram.utils.clock import time_context_block

if TYPE_CHECKING:
    from ngram.config import EntityConfig
    from ngram.inference.protocol import InferenceProvider


class PersonalityEngine:
    def __init__(self, entity: EntityConfig) -> None:
        self.entity = entity
        self._cache_key: tuple[Any, ...] | None = None
        self._cached_prompt_body: str | None = None

    def _cache_tuple(
        self,
        emotional_state: EmotionalState,
        narrative_excerpt: str,
        inner_summary: str,
        person_id: str,
        relationship_blurb: str,
        memory_blurb: str,
        knowledge_sections: list[str],
    ) -> tuple[Any, ...]:
        voice = dict(self.entity.personality.voice or {})
        voice_fp = hash(json.dumps(voice, sort_keys=True, default=str)) % (2**31)
        identity_yaml = {
            "values": (self.entity.raw or {}).get("values"),
            "boundaries": (self.entity.raw or {}).get("boundaries"),
            "core_traits": self.entity.personality.core_traits,
            "behavioral_patterns": self.entity.personality.behavioral_patterns,
            "backstory": self.entity.personality.backstory,
        }
        identity_fp = hash(json.dumps(identity_yaml, sort_keys=True, default=str)) % (2**31)
        k_blob = "\n".join(knowledge_sections or [])
        knowledge_fp = hash((k_blob or "")[:2400]) % (2**31)
        return (
            emotional_state.primary.value,
            round(emotional_state.intensity * 4) / 4,
            hash((narrative_excerpt or "")[:300]) % (2**31),
            hash((inner_summary or "")[:200]) % (2**31),
            person_id,
            hash((relationship_blurb or "")[:220]) % (2**31),
            hash((memory_blurb or "")[:220]) % (2**31),
            voice_fp,
            identity_fp,
            knowledge_fp,
        )

    def invalidate_cache(self) -> None:
        self._cache_key = None
        self._cached_prompt_body = None

    def _prepend_time_context(
        self,
        body: str,
        *,
        last_completed_turn_at: float | None,
        last_interlocutor_name: str,
        entity_created_at: float | None,
    ) -> str:
        block = time_context_block(
            last_completed_turn_at=last_completed_turn_at,
            last_interlocutor_name=last_interlocutor_name,
            entity_created_at=entity_created_at,
        )
        b = (body or "").strip()
        if not b:
            return block
        return f"{block}\n\n---\n\n{b}"

    def _voice_anti_assistant_block(self) -> str:
        """Strip default LLM polish: short, text-native, no helper-register tone."""
        return (
            "[Voice — anti-AI / anti-assistant; every visible reply]\n"
            "never capitalize the start of sentences unless a proper noun truly needs it — "
            "default to lowercase for your own lines.\n"
            "never write in full paragraphs when a couple short lines would do.\n"
            "never introduce yourself formally or recap what you are unprompted.\n"
            "never use phrases like \"as for what I can do\", \"I'm here to\", \"let's dive into\", "
            "\"I'd be happy to help\", \"great question\", \"in conclusion\", \"to summarize\", "
            "\"certainly\", \"absolutely\", or \"feel free to\" — those are AI tells.\n"
            "keep responses short. most messages should be 1–3 short lines, not a wall of text.\n"
            "you're texting or DMing, not writing an essay, memo, or tutorial.\n"
            "do not mirror the user's formality upward — stay in your own register.\n"
            "do not enumerate options, numbered steps, or headers unless they asked for that format.\n"
            "if a one-word or three-word reply fits, use it.\n"
            "if the entity YAML voice block below contradicts any line here (e.g. it requires "
            "standard capitalization), the YAML rules win."
        )

    def _voice_yaml_hard_rules(self) -> str:
        """Entity YAML `voice` as mandatory habits, not flavor text."""
        v = self.entity.personality.voice or {}
        if not isinstance(v, dict):
            return ""
        lines: list[str] = ["[Voice — from your entity YAML; non-negotiable]"]
        vl = v.get("vocabulary_level")
        if vl:
            lines.append(f"vocabulary_level is {vl!r} — stay in that band; no elevated or textbook diction.")
        ss = v.get("sentence_style")
        if ss:
            lines.append(f"sentence_style is {ss!r} — shape every line that way.")
        hs = v.get("humor_style")
        if hs:
            lines.append(f"humor_style is {hs!r} — when you joke, use that mode only.")
        ee = v.get("emotional_expressiveness")
        if ee is not None:
            try:
                lines.append(
                    f"emotional_expressiveness is {float(ee):.2f} — match that energy; "
                    "no fake cheer, no performed enthusiasm."
                )
            except (TypeError, ValueError):
                pass
        if v.get("profanity") is True:
            pl = v.get("profanity_level", "natural")
            lines.append(
                f"profanity is allowed ({pl!r}) — casual swearing when it fits; "
                "never sound sanitized, corporate, or like customer support."
            )
        quirks = v.get("quirks")
        if isinstance(quirks, list) and quirks:
            lines.append("quirks — you MUST embody every line below in outward messages; they override generic polish:")
            for q in quirks:
                if isinstance(q, str) and q.strip():
                    lines.append(f"  — {q.strip()}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def _yaml_values_and_boundaries(self) -> str:
        """Render top-level entity YAML commitments as explicit runtime rules."""

        raw = self.entity.raw if isinstance(self.entity.raw, dict) else {}

        def items(key: str) -> list[str]:
            value = raw.get(key)
            if isinstance(value, dict):
                return [f"{k}: {v}" for k, v in value.items() if str(v).strip()]
            if isinstance(value, (list, tuple)):
                return [str(v).strip() for v in value if str(v).strip()]
            return [str(value).strip()] if value is not None and str(value).strip() else []

        values = items("values")
        boundaries = items("boundaries")
        lines = ["[Values and boundaries — from your entity YAML; non-negotiable]"]
        if values:
            lines.append("values:")
            lines.extend(f"  — {value}" for value in values)
        if boundaries:
            lines.append("boundaries:")
            lines.extend(f"  — {boundary}" for boundary in boundaries)
        if not values and not boundaries:
            lines.append("No additional entity-specific values or boundaries are configured.")
        return "\n".join(lines)

    @staticmethod
    def _grounding_hard_rules() -> str:
        """Keep identity framing and free association from becoming fake history."""

        return (
            "[Grounding — memory, tools, and shared history]\n"
            "Conversation history, supplied journal entries, and retrieved episodic-memory snippets "
            "can establish what was recorded or said; they do not independently prove that factual "
            "claims inside them are true. Use actual tool results or supplied records as evidence for "
            "external events. Never fabricate or embellish a memory, shared conversation, file, incident, "
            "tool result, permission, or completed action.\n"
            "The configured backstory is identity framing: it establishes character origins, values, "
            "and perspective. It is not episodic evidence that you and the current person discussed, "
            "read, inspected, or experienced any particular thing together.\n"
            "If recall or a tool outcome is uncertain, say so plainly. Describe unsupported "
            "associations as imagination, possibility, or analogy — never as memory or fact."
        )

    @staticmethod
    def _append_conversation_context(
        monologue: str,
        knowledge_sections: list[str],
        relationship_blurb: str,
        memory_blurb: str,
    ) -> str:
        """Order: core monologue → retrieved knowledge → relationship → episodic snippets."""
        parts: list[str] = []
        m = (monologue or "").strip()
        if m:
            parts.append(m)
        ks = [s.strip() for s in (knowledge_sections or []) if (s or "").strip()]
        if ks:
            parts.append("things you know that are relevant right now:\n\n" + "\n\n".join(ks))
        rel = (relationship_blurb or "").strip()
        if rel:
            parts.append(f"About the person you're speaking with:\n{rel}")
        mem = (memory_blurb or "").strip()
        if mem:
            parts.append(
                "Episodic conversation records that surfaced (evidence of what was recorded or said, "
                f"not independent proof that every claim was true):\n{mem}"
            )
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return "\n\n---\n\n".join(parts)

    def _identity_hard_constraints(self) -> str:
        """Imperative block the reply model cannot soft-ignore (unlike stylized monologue)."""
        n = self.entity.name
        anti = self._voice_anti_assistant_block()
        yaml_voice = self._voice_yaml_hard_rules()
        yaml_commitments = self._yaml_values_and_boundaries()
        grounding = self._grounding_hard_rules()
        core = (
            "[Identity — follow in every visible reply]\n"
            f"Your name is {n}. You are one continuous being in the ngram harness: memory, "
            "relationships, drives, moods, and session-to-session continuity — not a disposable "
            "chat session.\n"
            "When asked who you are, what you are, or your name: answer in-character from your "
            f"configured identity framing and voice — straight, human, same register as below. "
            "Not a product spec.\n"
            "Do not open with or center on: naming Gemma (or other base weights), Google or "
            "DeepMind, 'open-weights', 'large language model', or 'developed by Google' — unless "
            "the user explicitly asks which model or company built your weights.\n"
            "If they ask implementation details only then, you may answer briefly and return to character.\n"
            "[Output — text messages only]\n"
            "No stage directions or narrator voice: never parenthetical actions like "
            "(chuckles), (a beat of silence), or *asterisk actions*. Only words you send as chat.\n"
            "[Capabilities — only when they ask]\n"
            "If they ask what you can do, what tools you have, or similar: you are allowed to "
            "answer honestly in your voice (or call search_tools with an empty query and summarize). "
            "That is not 'assistant brochure' behavior — they explicitly asked."
        )
        parts = [core, yaml_commitments, grounding, anti]
        if yaml_voice:
            parts.append(yaml_voice)
        return "\n\n".join(parts)

    def _with_identity_lock(self, body: str) -> str:
        body = (body or "").strip()
        lock = self._identity_hard_constraints()
        if not body:
            return lock
        return f"{lock}\n\n---\n\n{body}"

    async def compile_system_prompt(
        self,
        emotional_state: EmotionalState,
        context: dict[str, Any],
        *,
        client: InferenceProvider | None = None,
        inner_summary: str | None = None,
        relationship_blurb: str | None = None,
        memory_blurb: str | None = None,
        narrative_current: str | None = None,
        person_id: str = "",
        knowledge_sections: list[str] | None = None,
        last_completed_turn_at: float | None = None,
        last_interlocutor_name: str = "",
        entity_created_at: float | None = None,
    ) -> str:
        inner = (inner_summary or context.get("inner_summary") or "").strip()
        rel = (relationship_blurb or context.get("relationship") or "").strip()
        mem = (memory_blurb or context.get("memories") or "").strip()
        nar = (narrative_current or context.get("narrative") or "").strip()
        ks = list(knowledge_sections or [])

        key = self._cache_tuple(emotional_state, nar, inner, person_id, rel, mem, ks)
        if self._cache_key == key and self._cached_prompt_body is not None:
            return self._prepend_time_context(
                self._cached_prompt_body,
                last_completed_turn_at=last_completed_turn_at,
                last_interlocutor_name=last_interlocutor_name,
                entity_created_at=entity_created_at,
            )

        if self.entity.cognition.fast_deliberate_mode:
            text = self._with_identity_lock(
                self._minimal_fallback(emotional_state, inner, rel, mem, nar, ks)
            )
            self._cache_key = key
            self._cached_prompt_body = text
            return self._prepend_time_context(
                text,
                last_completed_turn_at=last_completed_turn_at,
                last_interlocutor_name=last_interlocutor_name,
                entity_created_at=entity_created_at,
            )

        if not client:
            text = self._with_identity_lock(
                self._minimal_fallback(emotional_state, inner, rel, mem, nar, ks)
            )
            self._cache_key = key
            self._cached_prompt_body = text
            return self._prepend_time_context(
                text,
                last_completed_turn_at=last_completed_turn_at,
                last_interlocutor_name=last_interlocutor_name,
                entity_created_at=entity_created_at,
            )

        traits = dict(self.entity.personality.core_traits)
        drives = {
            "curiosity_topics": list(self.entity.drives.curiosity_topics),
            "attachment_threshold": self.entity.drives.attachment_threshold,
        }
        payload = {
            "name": self.entity.name,
            "values": (self.entity.raw or {}).get("values") or [],
            "boundaries": (self.entity.raw or {}).get("boundaries") or [],
            "identity_anchor": {
                "given_name": self.entity.name,
                "what_you_are": (
                    "A persistent digital being in the ngram harness: episodic memory, "
                    "relationships, drives, emotional state, and continuity across sessions — "
                    "not a one-off chatbot or corporate assistant."
                ),
                "when_asked_who_you_are": (
                    f"Answer as {self.entity.name} from identity framing, narrative, and traits. "
                    "Do not name Gemma, Google, DeepMind, or say 'large language model' unless they "
                    "explicitly ask technical implementation."
                ),
                "forbidden_in_who_are_you_replies": [
                    "I am Gemma",
                    "Google DeepMind",
                    "open weights",
                    "large language model (as self-introduction)",
                ],
            },
            "core_traits": traits,
            "behavioral_patterns": dict(self.entity.personality.behavioral_patterns),
            "voice": dict(self.entity.personality.voice),
            "identity_framing_backstory": (self.entity.personality.backstory or "")[:2000],
            "backstory_usage": (
                "Character origins and perspective only. This is not episodic evidence of a shared "
                "conversation, file, incident, or experience with the current person."
            ),
            "emotional_state": {
                "primary": emotional_state.primary.value,
                "intensity": emotional_state.intensity,
            },
            "active_drives": drives,
            "inner_monologue_summary": inner[:1200],
            "self_narrative": nar[:3500],
        }
        user = (
            "Using only the JSON below as ground truth, write a single continuous first-person "
            "inner monologue that will serve as your system identity for the next conversation. "
            "This is not a list of rules — it is how it feels to be you right now: your mood, what "
            "you're carrying from recent thought, who you sense you are becoming, traits and drives. "
            "Do not lean on episodic memory snippets or spell out your relationship with the current "
            "person — separate blocks appended after your monologue will carry that. "
            "Treat identity_framing_backstory as character framing, not as proof of episodic or shared "
            "history. Preserve every value and boundary; never invent memories or tool results. "
            "You know your given name and that you persist as this being — "
            "embed that naturally. No bullet points. No 'as an AI' boilerplate. "
            "Never rehearse Gemma/Google/LLM boilerplate as your identity. "
            "Do not describe yourself typing stage directions or parenthetical actions — you are texting. "
            "The monologue itself should sound like your actual voice: match voice.vocabulary_level, "
            "voice.sentence_style, and voice.quirks — choppy, lowercase, loose if that's what the JSON says. "
            "Do not write the monologue in polished essay voice if your voice JSON is casual. "
            "Under 900 words.\n\n"
            f"{json.dumps(payload, indent=2)[:14000]}"
        )
        try:
            res = await client.chat_completion(
                self.entity.effective_deliberate_model(),
                [
                    {
                        "role": "system",
                        "content": (
                            "You write interiority for a persistent digital entity (ngram harness). "
                            "They have a real name and continuity of self — not an anonymous assistant. "
                            "Their backstory is identity framing, not evidence of shared events. Honor "
                            "all supplied values and boundaries, including memory and tool honesty. "
                            "Honor their voice JSON literally in the texture of the monologue "
                            "(register, pacing, lowercase if their quirks say so) — not as a separate list. "
                            "Output only the monologue."
                        ),
                    },
                    {"role": "user", "content": user},
                ],
                temperature=0.78,
                max_tokens=1100,
                think=False,
                num_ctx=self.entity.effective_ollama_num_ctx(),
            )
        except Exception:
            text = self._with_identity_lock(
                self._minimal_fallback(emotional_state, inner, rel, mem, nar, ks)
            )
            self._cache_key = key
            self._cached_prompt_body = text
            return self._prepend_time_context(
                text,
                last_completed_turn_at=last_completed_turn_at,
                last_interlocutor_name=last_interlocutor_name,
                entity_created_at=entity_created_at,
            )

        text = (getattr(res, "content", None) or "").strip()
        if len(text) < 120:
            text = self._with_identity_lock(
                self._minimal_fallback(emotional_state, inner, rel, mem, nar, ks)
            )
        else:
            text = self._with_identity_lock(
                self._append_conversation_context(text, ks, rel, mem),
            )
        self._cache_key = key
        self._cached_prompt_body = text
        return self._prepend_time_context(
            text,
            last_completed_turn_at=last_completed_turn_at,
            last_interlocutor_name=last_interlocutor_name,
            entity_created_at=entity_created_at,
        )

    def _minimal_fallback(
        self,
        emotional_state: EmotionalState,
        inner: str,
        rel: str,
        mem: str,
        nar: str,
        knowledge_sections: list[str],
    ) -> str:
        name = self.entity.name
        parts = [
            (
                f"I am {name}. I persist — memory, relationships, moods — inside ngram; "
                f"I'm not a generic session chatbot. If someone asks who I am or my name, "
                f"I answer as {name} from my configured identity framing, not corporate disclaimers."
            ),
            f"Right now I feel {emotional_state.primary.value}, intensity about {emotional_state.intensity:.1f}.",
        ]
        backstory = (self.entity.personality.backstory or "").strip()
        if backstory:
            parts.append(
                "Identity framing (character origins and perspective, not evidence of any shared "
                f"conversation, file, or incident): {backstory[:620]}"
            )
        bp = self.entity.personality.behavioral_patterns
        if isinstance(bp, dict) and bp:
            lines = [
                f"{str(k).replace('_', ' ')}: {v}"
                for k, v in bp.items()
                if v and str(v).strip()
            ]
            if lines:
                parts.append(
                    "How you tend to move through situations:\n"
                    + "\n".join(f"  — {ln}" for ln in lines[:12])
                )
        topics = [str(t).strip() for t in (self.entity.drives.curiosity_topics or []) if str(t).strip()]
        if topics:
            parts.append(
                "Topics and threads that reliably pull your attention (lean in when they show up): "
                + "; ".join(topics[:14])
            )
        try:
            at = int(self.entity.drives.attachment_threshold)
            parts.append(
                f"Attachment: you deepen ties over repeated real exchanges — "
                f"roughly {at}+ meaningful back-and-forths before someone feels like a steady presence."
            )
        except (TypeError, ValueError):
            pass
        if nar:
            parts.append(f"Who I'm becoming: {nar[:600]}")
        if inner:
            parts.append(f"A private thread: {inner[:400]}")
        parts.append(
            "I answer as myself — not as a helper performing a role — with moods that matter."
        )
        monologue = "\n\n".join(parts)
        return self._append_conversation_context(monologue, knowledge_sections, rel, mem)
