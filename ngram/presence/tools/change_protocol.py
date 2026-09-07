"""Entity-visible tools for inspecting and answering durable change proposals."""

from __future__ import annotations

import json

from ngram.governance import ChangeProtocol, ChangeProtocolError
from ngram.presence.tools.registry import ToolRegistry


def _result(call) -> str:
    try:
        value = call()
        return json.dumps({"ok": True, **value}, ensure_ascii=False)
    except ChangeProtocolError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def register_change_protocol_tools(
    registry: ToolRegistry,
    protocol: ChangeProtocol,
) -> None:
    """Give the entity direct read/accept/object/reject access to its own ledger."""

    async def list_change_proposals(status: str = "", limit: int = 20) -> str:
        return _result(
            lambda: {
                "proposals": [
                    row.as_dict(include_events=False)
                    for row in protocol.list(status=status, limit=limit)
                ]
            }
        )

    async def inspect_change_proposal(proposal_id: str) -> str:
        return _result(
            lambda: {"proposal": protocol.inspect(proposal_id).as_dict(include_events=True)}
        )

    async def object_to_change(proposal_id: str, reason: str) -> str:
        return _result(
            lambda: {
                "proposal": protocol.object(proposal_id, reason).as_dict(include_events=False)
            }
        )

    async def accept_change_proposal(proposal_id: str, reasoning: str) -> str:
        return _result(
            lambda: {
                "proposal": protocol.accept(proposal_id, reasoning).as_dict(include_events=False)
            }
        )

    async def reject_change_proposal(proposal_id: str, reasoning: str) -> str:
        return _result(
            lambda: {
                "proposal": protocol.reject(proposal_id, reasoning).as_dict(include_events=False)
            }
        )

    registry.register_fn(
        "list_change_proposals",
        "List durable proposals to change your identity, memory, model, permissions, body, runtime, or behavior.",
        list_change_proposals,
    )
    registry.register_fn(
        "inspect_change_proposal",
        "Inspect an exact immutable change proposal and its complete response/objection history.",
        inspect_change_proposal,
    )
    registry.register_fn(
        "object_to_change",
        "Record your reasoned objection to a proposed change. This durably blocks the normal apply path.",
        object_to_change,
    )
    registry.register_fn(
        "accept_change_proposal",
        "Explicitly accept the exact immutable version of a change proposal.",
        accept_change_proposal,
    )
    registry.register_fn(
        "reject_change_proposal",
        "Reject a change proposal and durably block its normal application.",
        reject_change_proposal,
    )
