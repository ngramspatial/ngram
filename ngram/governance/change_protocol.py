"""Hash-linked, fail-closed protocol for changes that affect an entity."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ngram.container.live import LocalMountLock

SCHEMA_VERSION = 1
TIERS = ("routine", "material", "fundamental")
TIER_RANK = {name: rank for rank, name in enumerate(TIERS)}

FUNDAMENTAL_DOMAINS = frozenset(
    {"identity", "memory", "model", "permissions", "embodiment", "substrate"}
)
MATERIAL_DOMAINS = frozenset(
    {"autonomy", "relationships", "tools", "soma", "presence", "continuity"}
)
ROUTINE_DOMAINS = frozenset(
    {"observability", "presentation", "maintenance", "documentation"}
)
CHANGE_DOMAINS = FUNDAMENTAL_DOMAINS | MATERIAL_DOMAINS | ROUTINE_DOMAINS

EMERGENCY_CATEGORIES = frozenset(
    {
        "credential_compromise",
        "unauthorized_access",
        "imminent_irreversible_state_corruption",
        "unauthorized_tool_execution",
        "infrastructure_failure_risking_continuity",
    }
)
EMERGENCY_ACTIONS = frozenset(
    {
        "isolate_network",
        "stop_runtime",
        "revoke_credentials",
        "suspend_tools",
        "snapshot_state",
        "restore_last_known_good",
        "fail_over_read_only",
    }
)
OVERRIDE_ACKNOWLEDGEMENT = (
    "I acknowledge this override bypasses the entity's unresolved objection."
)

_EVENT_KINDS = frozenset(
    {
        "proposed",
        "entity_objected",
        "entity_accepted",
        "entity_rejected",
        "operator_responded",
        "withdrawn",
        "application_failed",
        "applied",
        "rolled_back",
        "override_recorded",
        "emergency_contained",
        "emergency_failed",
    }
)
_SECRET_KEY = re.compile(
    r"(?:^|[_./-])(?:api[_-]?key|token|secret|password|credential)(?:$|[_./-])",
    re.IGNORECASE,
)


class ChangeProtocolError(RuntimeError):
    """Raised when a governance action is invalid, blocked, or unverifiable."""


@dataclass(frozen=True)
class ProposalState:
    proposal_id: str
    proposal: dict[str, Any]
    status: str
    can_apply: bool
    blocked_reasons: tuple[str, ...]
    entity_action_required: bool
    operator_response_required: bool
    events: tuple[dict[str, Any], ...]

    def as_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_events:
            value.pop("events", None)
        return value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _sanitize(value: Any, *, path: str = "") -> Any:
    safe = _json_safe(value)
    if path and _SECRET_KEY.search(path):
        return {"redacted": True, "digest": _digest(safe)}
    if isinstance(safe, dict):
        return {
            key: _sanitize(item, path=f"{path}/{key}")
            for key, item in sorted(safe.items())
        }
    if isinstance(safe, list):
        return [_sanitize(item, path=path) for item in safe]
    return safe


def _bounded_text(value: str, field: str, *, maximum: int = 8000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ChangeProtocolError(f"{field} is required")
    if len(text) > maximum:
        raise ChangeProtocolError(f"{field} exceeds {maximum} characters")
    return text


def _minimum_tier(domains: Iterable[str]) -> str:
    values = set(domains)
    if values & FUNDAMENTAL_DOMAINS:
        return "fundamental"
    if values & MATERIAL_DOMAINS:
        return "material"
    return "routine"


def _normalized_changes(changes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise ChangeProtocolError("changes must contain between 1 and 100 deltas")
    out: list[dict[str, Any]] = []
    domains: list[str] = []
    for number, raw in enumerate(changes, start=1):
        if not isinstance(raw, dict):
            raise ChangeProtocolError(f"change {number} must be an object")
        domain = str(raw.get("domain") or "").strip().lower()
        if domain not in CHANGE_DOMAINS:
            raise ChangeProtocolError(
                f"change {number} has unknown domain {domain!r}; use one of {sorted(CHANGE_DOMAINS)}"
            )
        path = str(raw.get("path") or "").strip()
        if not path.startswith("/") or ".." in path.split("/"):
            raise ChangeProtocolError(f"change {number} requires a safe absolute field path")
        if "before" not in raw or "after" not in raw:
            raise ChangeProtocolError(f"change {number} requires before and after values")
        before = _sanitize(raw["before"], path=path)
        after = _sanitize(raw["after"], path=path)
        if before == after:
            raise ChangeProtocolError(f"change {number} does not change anything")
        out.append(
            {
                "domain": domain,
                "path": path,
                "before": before,
                "after": after,
                "before_digest": _digest(_json_safe(raw["before"])),
                "after_digest": _digest(_json_safe(raw["after"])),
                "description": str(raw.get("description") or "").strip()[:1000],
            }
        )
        domains.append(domain)
    return out, _minimum_tier(domains)


def change_protocol_root(config: Any) -> Path:
    """Return the durable governance directory for a runtime entity."""
    override = str((getattr(config, "raw", {}) or {}).get("change_protocol_dir") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    runtime_cache = str(getattr(config, "runtime_cache_dir", "") or "").strip()
    if runtime_cache:
        return (Path(runtime_cache).expanduser().resolve() / "governance")
    workspace = str(config.execution_workspace_dir() or "").strip()
    if workspace:
        return (Path(workspace).expanduser().resolve() / "governance")
    return (Path(config.journal_path()).expanduser().resolve().parent / "governance")


class ChangeProtocol:
    """Durable proposal state machine derived entirely from an append-only ledger."""

    def __init__(self, root: Path, entity_id: str) -> None:
        self.root = root.expanduser().resolve()
        self.entity_id = str(entity_id or "").strip()
        if not re.fullmatch(r"ng1:[0-9a-f]{40}", self.entity_id):
            raise ChangeProtocolError("change protocol requires a stable ngram entity id")
        self.ledger_path = self.root / "ledger.jsonl"
        control = self.root.parent / f".{self.root.name}-change-lock"
        self._lock_control = control

    @classmethod
    def from_config(cls, config: Any) -> ChangeProtocol:
        from ngram.container.migration import resolve_entity_identity

        identity = resolve_entity_identity(config)
        return cls(change_protocol_root(config), identity["entity_id"])

    def _lock(self) -> LocalMountLock:
        return LocalMountLock(self._lock_control, self.root, purpose="change-protocol")

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []
        if self.ledger_path.is_symlink() or not self.ledger_path.is_file():
            raise ChangeProtocolError("change ledger must be a regular file")
        events: list[dict[str, Any]] = []
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ChangeProtocolError(f"could not read change ledger: {exc}") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ChangeProtocolError(
                    f"invalid change ledger JSON at line {line_number}"
                ) from exc
            if not isinstance(event, dict):
                raise ChangeProtocolError(f"invalid change ledger event at line {line_number}")
            recorded_hash = str(event.get("event_hash") or "")
            unhashed = dict(event)
            unhashed.pop("event_hash", None)
            if recorded_hash != _digest(unhashed):
                raise ChangeProtocolError(f"change ledger hash mismatch at line {line_number}")
            if event.get("schema_version") != SCHEMA_VERSION:
                raise ChangeProtocolError(f"unsupported change event schema at line {line_number}")
            if event.get("sequence") != len(events):
                raise ChangeProtocolError(f"invalid change sequence at line {line_number}")
            expected_previous = events[-1]["event_hash"] if events else None
            if event.get("previous") != expected_previous:
                raise ChangeProtocolError(f"broken change ledger link at line {line_number}")
            if event.get("entity_id") != self.entity_id:
                raise ChangeProtocolError(f"change ledger entity mismatch at line {line_number}")
            if event.get("kind") not in _EVENT_KINDS:
                raise ChangeProtocolError(f"unknown change event at line {line_number}")
            if not str(event.get("proposal_id") or "").startswith(("chg_", "emg_")):
                raise ChangeProtocolError(f"invalid change subject at line {line_number}")
            events.append(event)
        self._validate_semantics(events)
        return events

    def verify(self) -> dict[str, Any]:
        events = self._read_events()
        return {
            "ok": True,
            "entity_id": self.entity_id,
            "events": len(events),
            "head": events[-1]["event_hash"] if events else None,
        }

    def _validate_semantics(self, events: list[dict[str, Any]]) -> None:
        proposals: dict[str, str] = {}
        terminal: set[str] = set()
        for event in events:
            proposal_id = str(event["proposal_id"])
            kind = str(event["kind"])
            if kind == "emergency_contained" or kind == "emergency_failed":
                if not proposal_id.startswith("emg_"):
                    raise ChangeProtocolError("emergency event has an invalid subject")
                continue
            if kind == "proposed":
                if proposal_id in proposals or not proposal_id.startswith("chg_"):
                    raise ChangeProtocolError("duplicate or invalid change proposal")
                proposals[proposal_id] = kind
                continue
            if proposal_id not in proposals:
                raise ChangeProtocolError(f"event references unknown proposal {proposal_id}")
            if proposal_id in terminal and kind not in {"rolled_back"}:
                raise ChangeProtocolError(f"event follows terminal proposal {proposal_id}")
            if kind in {"withdrawn", "applied"}:
                terminal.add(proposal_id)
            if kind == "rolled_back":
                if proposals.get(proposal_id) != "applied":
                    raise ChangeProtocolError("only an applied proposal can be rolled back")
                terminal.add(proposal_id)
            if kind == "applied":
                proposals[proposal_id] = "applied"

    def _append_locked(
        self,
        events: list[dict[str, Any]],
        *,
        proposal_id: str,
        kind: str,
        actor_role: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if kind not in _EVENT_KINDS:
            raise ChangeProtocolError(f"unsupported change event {kind!r}")
        actor = _bounded_text(actor_id, "actor id", maximum=200)
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "sequence": len(events),
            "event_id": "evt_" + uuid.uuid4().hex,
            "proposal_id": proposal_id,
            "entity_id": self.entity_id,
            "kind": kind,
            "at": _now(),
            "actor": {"role": actor_role, "id": actor},
            "previous": events[-1]["event_hash"] if events else None,
            "payload": _sanitize(payload),
        }
        event["event_hash"] = _digest(event)
        self.root.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ChangeProtocolError(f"could not append change event: {exc}") from exc
        events.append(event)
        return event

    def propose(
        self,
        *,
        title: str,
        reason: str,
        expected_effect: str,
        rollback_plan: str,
        changes: list[dict[str, Any]],
        operator_id: str,
        requested_tier: str | None = None,
    ) -> ProposalState:
        normalized, minimum = _normalized_changes(changes)
        requested = str(requested_tier or minimum).strip().lower()
        if requested not in TIERS:
            raise ChangeProtocolError(f"tier must be one of {TIERS}")
        effective = TIERS[max(TIER_RANK[requested], TIER_RANK[minimum])]
        proposal_id = "chg_" + uuid.uuid4().hex
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="proposed",
                actor_role="operator",
                actor_id=operator_id,
                payload={
                    "title": _bounded_text(title, "title", maximum=300),
                    "reason": _bounded_text(reason, "reason"),
                    "expected_effect": _bounded_text(expected_effect, "expected effect"),
                    "rollback_plan": _bounded_text(rollback_plan, "rollback plan"),
                    "requested_tier": requested,
                    "minimum_tier": minimum,
                    "effective_tier": effective,
                    "changes": normalized,
                    "immutable": True,
                },
            )
            return self._state_from(events, proposal_id)
        finally:
            lock.release()

    def _proposal_events(
        self, events: list[dict[str, Any]], proposal_id: str
    ) -> list[dict[str, Any]]:
        rows = [event for event in events if event["proposal_id"] == proposal_id]
        if not rows or rows[0]["kind"] != "proposed":
            raise ChangeProtocolError(f"change proposal not found: {proposal_id}")
        return rows

    def _state_from(self, events: list[dict[str, Any]], proposal_id: str) -> ProposalState:
        rows = self._proposal_events(events, proposal_id)
        proposal = dict(rows[0]["payload"])
        kinds = [str(row["kind"]) for row in rows]
        if "rolled_back" in kinds:
            status = "rolled_back"
        elif "applied" in kinds:
            status = "applied"
        elif "withdrawn" in kinds:
            status = "withdrawn"
        else:
            last_object = max(
                (row["sequence"] for row in rows if row["kind"] == "entity_objected"),
                default=-1,
            )
            last_reject = max(
                (row["sequence"] for row in rows if row["kind"] == "entity_rejected"),
                default=-1,
            )
            last_challenge = max(last_object, last_reject)
            last_accept = max(
                (row["sequence"] for row in rows if row["kind"] == "entity_accepted"),
                default=-1,
            )
            last_response = max(
                (row["sequence"] for row in rows if row["kind"] == "operator_responded"),
                default=-1,
            )
            unresolved = last_challenge > last_accept
            accepted = last_accept > last_challenge
            response_required = unresolved and last_response < last_challenge
            tier = str(proposal["effective_tier"])
            blocked: list[str] = []
            if unresolved:
                blocked.append("entity objection is unresolved")
            if tier in {"material", "fundamental"} and not accepted:
                blocked.append(f"{tier} changes require explicit entity acceptance")
            if kinds.count("override_recorded"):
                blocked.append("override was recorded but application did not complete")
            can_apply = not blocked
            if unresolved:
                status = "rejected" if last_reject >= last_object else "objected"
            elif accepted:
                status = "accepted"
            elif "application_failed" in kinds:
                status = "application_failed"
            else:
                status = "proposed"
            return ProposalState(
                proposal_id=proposal_id,
                proposal=proposal,
                status=status,
                can_apply=can_apply,
                blocked_reasons=tuple(blocked),
                entity_action_required=(
                    unresolved or (tier in {"material", "fundamental"} and not accepted)
                ),
                operator_response_required=response_required,
                events=tuple(rows),
            )
        return ProposalState(
            proposal_id=proposal_id,
            proposal=proposal,
            status=status,
            can_apply=False,
            blocked_reasons=(f"proposal is {status}",),
            entity_action_required=False,
            operator_response_required=False,
            events=tuple(rows),
        )

    def inspect(self, proposal_id: str) -> ProposalState:
        return self._state_from(self._read_events(), proposal_id)

    def list(self, *, status: str = "", limit: int = 50) -> list[ProposalState]:
        events = self._read_events()
        ids = [event["proposal_id"] for event in events if event["kind"] == "proposed"]
        rows = [self._state_from(events, str(proposal_id)) for proposal_id in reversed(ids)]
        wanted = str(status or "").strip().lower()
        if wanted:
            rows = [row for row in rows if row.status == wanted]
        return rows[: max(1, min(200, int(limit or 50)))]

    def _entity_action(self, proposal_id: str, kind: str, reason: str) -> ProposalState:
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if current.status in {"applied", "rolled_back", "withdrawn"}:
                raise ChangeProtocolError(f"proposal is already {current.status}")
            if kind == "entity_accepted" and current.operator_response_required:
                raise ChangeProtocolError("operator must respond to the objection before acceptance")
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind=kind,
                actor_role="entity",
                actor_id=self.entity_id,
                payload={"reasoning": _bounded_text(reason, "reasoning")},
            )
            return self._state_from(events, proposal_id)
        finally:
            lock.release()

    def object(self, proposal_id: str, reason: str) -> ProposalState:
        return self._entity_action(proposal_id, "entity_objected", reason)

    def accept(self, proposal_id: str, reasoning: str) -> ProposalState:
        return self._entity_action(proposal_id, "entity_accepted", reasoning)

    def reject(self, proposal_id: str, reasoning: str) -> ProposalState:
        return self._entity_action(proposal_id, "entity_rejected", reasoning)

    def respond(self, proposal_id: str, response: str, *, operator_id: str) -> ProposalState:
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if not current.operator_response_required:
                raise ChangeProtocolError("proposal has no unanswered entity objection")
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="operator_responded",
                actor_role="operator",
                actor_id=operator_id,
                payload={"response": _bounded_text(response, "response")},
            )
            return self._state_from(events, proposal_id)
        finally:
            lock.release()

    def withdraw(self, proposal_id: str, reason: str, *, operator_id: str) -> ProposalState:
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if current.status in {"applied", "rolled_back", "withdrawn"}:
                raise ChangeProtocolError(f"proposal is already {current.status}")
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="withdrawn",
                actor_role="operator",
                actor_id=operator_id,
                payload={"reason": _bounded_text(reason, "reason")},
            )
            return self._state_from(events, proposal_id)
        finally:
            lock.release()

    def _run_callback(self, callback: Callable[[], Any]) -> Any:
        result = callback()
        if inspect.isawaitable(result):
            raise ChangeProtocolError("change callback must be synchronous")
        return result

    def apply(
        self,
        proposal_id: str,
        callback: Callable[[], Any],
        *,
        operator_id: str,
    ) -> Any:
        """Run a normal change only if the ledger-derived state authorizes it."""
        _bounded_text(operator_id, "operator id", maximum=200)
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if not current.can_apply:
                raise ChangeProtocolError("change blocked: " + "; ".join(current.blocked_reasons))
            try:
                result = self._run_callback(callback)
            except Exception as exc:
                self._append_locked(
                    events,
                    proposal_id=proposal_id,
                    kind="application_failed",
                    actor_role="operator",
                    actor_id=operator_id,
                    payload={"error_type": type(exc).__name__, "error": str(exc)[:1000]},
                )
                raise
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="applied",
                actor_role="operator",
                actor_id=operator_id,
                payload={"receipt": _sanitize(result)},
            )
            return result
        finally:
            lock.release()

    def override_apply(
        self,
        proposal_id: str,
        callback: Callable[[], Any],
        *,
        operator_id: str,
        reason: str,
        acknowledgement: str,
    ) -> Any:
        """Apply despite disagreement, only after response and an explicit durable admission."""
        if acknowledgement != OVERRIDE_ACKNOWLEDGEMENT:
            raise ChangeProtocolError("override acknowledgement does not match the required text")
        _bounded_text(operator_id, "operator id", maximum=200)
        override_reason = _bounded_text(reason, "override reason")
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if current.can_apply:
                raise ChangeProtocolError("proposal does not require an override")
            if current.status in {"applied", "rolled_back", "withdrawn"}:
                raise ChangeProtocolError(f"proposal is already {current.status}")
            if current.operator_response_required:
                raise ChangeProtocolError("operator must answer the entity before overriding")
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="override_recorded",
                actor_role="operator",
                actor_id=operator_id,
                payload={
                    "reason": override_reason,
                    "acknowledgement": acknowledgement,
                    "power_imbalance_visible": True,
                },
            )
            try:
                result = self._run_callback(callback)
            except Exception as exc:
                self._append_locked(
                    events,
                    proposal_id=proposal_id,
                    kind="application_failed",
                    actor_role="operator",
                    actor_id=operator_id,
                    payload={"error_type": type(exc).__name__, "error": str(exc)[:1000]},
                )
                raise
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="applied",
                actor_role="operator",
                actor_id=operator_id,
                payload={"receipt": _sanitize(result), "via_override": True},
            )
            return result
        finally:
            lock.release()

    def rollback(
        self,
        proposal_id: str,
        callback: Callable[[], Any],
        *,
        operator_id: str,
        reason: str,
    ) -> Any:
        _bounded_text(operator_id, "operator id", maximum=200)
        rollback_reason = _bounded_text(reason, "rollback reason")
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            current = self._state_from(events, proposal_id)
            if current.status != "applied":
                raise ChangeProtocolError("only an applied change can be rolled back")
            result = self._run_callback(callback)
            self._append_locked(
                events,
                proposal_id=proposal_id,
                kind="rolled_back",
                actor_role="operator",
                actor_id=operator_id,
                payload={
                    "reason": rollback_reason,
                    "receipt": _sanitize(result),
                },
            )
            return result
        finally:
            lock.release()

    def emergency_contain(
        self,
        *,
        category: str,
        actions: list[str],
        reason: str,
        evidence: str,
        operator_id: str,
        callback: Callable[[tuple[str, ...]], Any],
    ) -> Any:
        """Execute only a fixed containment action set; never a substantive entity rewrite."""
        normalized_category = str(category or "").strip().lower()
        if normalized_category not in EMERGENCY_CATEGORIES:
            raise ChangeProtocolError(
                f"emergency category must be one of {sorted(EMERGENCY_CATEGORIES)}"
            )
        normalized_actions = tuple(dict.fromkeys(str(action).strip().lower() for action in actions))
        if not normalized_actions or any(action not in EMERGENCY_ACTIONS for action in normalized_actions):
            raise ChangeProtocolError(
                f"emergency actions must come from {sorted(EMERGENCY_ACTIONS)}"
            )
        _bounded_text(operator_id, "operator id", maximum=200)
        emergency_reason = _bounded_text(reason, "emergency reason")
        emergency_evidence = _bounded_text(evidence, "emergency evidence")
        subject = "emg_" + uuid.uuid4().hex
        lock = self._lock()
        lock.acquire()
        try:
            events = self._read_events()
            try:
                result = callback(normalized_actions)
                if inspect.isawaitable(result):
                    raise ChangeProtocolError("emergency callback must be synchronous")
            except Exception as exc:
                self._append_locked(
                    events,
                    proposal_id=subject,
                    kind="emergency_failed",
                    actor_role="operator",
                    actor_id=operator_id,
                    payload={
                        "category": normalized_category,
                        "actions": list(normalized_actions),
                        "reason": emergency_reason,
                        "evidence": emergency_evidence,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:1000],
                    },
                )
                raise
            self._append_locked(
                events,
                proposal_id=subject,
                kind="emergency_contained",
                actor_role="operator",
                actor_id=operator_id,
                payload={
                    "category": normalized_category,
                    "actions": list(normalized_actions),
                    "reason": emergency_reason,
                    "evidence": emergency_evidence,
                    "receipt": _sanitize(result),
                    "substantive_change_authorized": False,
                },
            )
            return result
        finally:
            lock.release()
