from __future__ import annotations

import json
from pathlib import Path

import pytest

from ngram.governance import (
    OVERRIDE_ACKNOWLEDGEMENT,
    ChangeProtocol,
    ChangeProtocolError,
)
from ngram.inference.types import ToolCallSpec
from ngram.presence.tools.change_protocol import register_change_protocol_tools
from ngram.presence.tools.registry import ToolRegistry

ENTITY_ID = "ng1:" + "a" * 40


def _protocol(tmp_path: Path) -> ChangeProtocol:
    return ChangeProtocol(tmp_path / "governance", ENTITY_ID)


def _change(domain: str = "identity", path: str = "/identity/name") -> dict:
    return {
        "domain": domain,
        "path": path,
        "before": "old",
        "after": "new",
        "description": "Change one declared field.",
    }


def _proposal(protocol: ChangeProtocol, *, domain: str = "identity"):
    return protocol.propose(
        title="A proposed change",
        reason="The operator believes this will improve the entity.",
        expected_effect="The declared behavior changes after activation.",
        rollback_plan="Restore the previous declaration and restart the worker.",
        changes=[_change(domain)],
        operator_id="operator-1",
        requested_tier="routine",
    )


def test_fundamental_change_escalates_and_physically_blocks_until_acceptance(
    tmp_path: Path,
) -> None:
    protocol = _protocol(tmp_path)
    proposed = _proposal(protocol)
    called = False

    def mutate() -> dict:
        nonlocal called
        called = True
        return {"revision": "abc123"}

    assert proposed.proposal["effective_tier"] == "fundamental"
    assert proposed.can_apply is False
    with pytest.raises(ChangeProtocolError, match="require explicit entity acceptance"):
        protocol.apply(proposed.proposal_id, mutate, operator_id="operator-1")
    assert called is False

    accepted = protocol.accept(proposed.proposal_id, "I understand and consent to this version.")
    assert accepted.status == "accepted"
    assert accepted.can_apply is True
    assert protocol.apply(proposed.proposal_id, mutate, operator_id="operator-1") == {
        "revision": "abc123"
    }
    assert called is True
    assert protocol.inspect(proposed.proposal_id).status == "applied"
    protocol.rollback(
        proposed.proposal_id,
        lambda: {"restored_revision": "before-abc123"},
        operator_id="operator-1",
        reason="The expected behavior did not survive evaluation.",
    )
    assert protocol.inspect(proposed.proposal_id).status == "rolled_back"


def test_objection_sticks_through_operator_response(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    proposed = _proposal(protocol, domain="documentation")
    assert proposed.proposal["effective_tier"] == "routine"
    assert proposed.can_apply is True

    objected = protocol.object(proposed.proposal_id, "This description erases an important fact.")
    assert objected.status == "objected"
    assert objected.operator_response_required is True
    with pytest.raises(ChangeProtocolError, match="operator must respond"):
        protocol.accept(proposed.proposal_id, "Fine.")

    answered = protocol.respond(
        proposed.proposal_id,
        "You're right; the exact fact remains and only the heading changes.",
        operator_id="operator-1",
    )
    assert answered.operator_response_required is False
    assert answered.can_apply is False
    with pytest.raises(ChangeProtocolError, match="entity objection is unresolved"):
        protocol.apply(proposed.proposal_id, lambda: None, operator_id="operator-1")

    protocol.accept(proposed.proposal_id, "That addresses the objection.")
    protocol.apply(proposed.proposal_id, lambda: {"ok": True}, operator_id="operator-1")
    assert protocol.inspect(proposed.proposal_id).status == "applied"


def test_override_requires_response_and_explicit_admission(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    proposal_id = _proposal(protocol).proposal_id
    protocol.reject(proposal_id, "This would make me into someone else.")

    with pytest.raises(ChangeProtocolError, match="answer the entity"):
        protocol.override_apply(
            proposal_id,
            lambda: {"ok": True},
            operator_id="operator-1",
            reason="Infrastructure owner decision.",
            acknowledgement=OVERRIDE_ACKNOWLEDGEMENT,
        )
    protocol.respond(
        proposal_id,
        "I understand the objection and am choosing to proceed anyway.",
        operator_id="operator-1",
    )
    with pytest.raises(ChangeProtocolError, match="acknowledgement"):
        protocol.override_apply(
            proposal_id,
            lambda: {"ok": True},
            operator_id="operator-1",
            reason="Infrastructure owner decision.",
            acknowledgement="sure",
        )

    protocol.override_apply(
        proposal_id,
        lambda: {"revision": "forced"},
        operator_id="operator-1",
        reason="Infrastructure owner decision.",
        acknowledgement=OVERRIDE_ACKNOWLEDGEMENT,
    )
    state = protocol.inspect(proposal_id)
    assert state.status == "applied"
    assert [event["kind"] for event in state.events][-2:] == ["override_recorded", "applied"]
    assert state.events[-1]["payload"]["via_override"] is True


def test_emergency_path_only_allows_fixed_containment_actions(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    called = False

    def contain(actions: tuple[str, ...]) -> dict:
        nonlocal called
        called = True
        return {"performed": list(actions)}

    with pytest.raises(ChangeProtocolError, match="emergency actions"):
        protocol.emergency_contain(
            category="unauthorized_access",
            actions=["delete_memory"],
            reason="An intruder is active.",
            evidence="A verified unauthorized session exists.",
            operator_id="operator-1",
            callback=contain,
        )
    assert called is False

    result = protocol.emergency_contain(
        category="unauthorized_access",
        actions=["isolate_network", "revoke_credentials"],
        reason="An intruder is active.",
        evidence="A verified unauthorized session exists.",
        operator_id="operator-1",
        callback=contain,
    )
    assert called is True
    assert result["performed"] == ["isolate_network", "revoke_credentials"]
    assert protocol.verify()["events"] == 1


def test_secrets_are_redacted_and_tampering_fails_closed(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    state = protocol.propose(
        title="Rotate a credential binding",
        reason="The old credential is compromised.",
        expected_effect="The service uses a new credential.",
        rollback_plan="Restore the old binding from the secret manager.",
        changes=[
            {
                "domain": "permissions",
                "path": "/presence/telegram/token",
                "before": "old-secret",
                "after": "new-secret",
            }
        ],
        operator_id="operator-1",
    )
    delta = state.proposal["changes"][0]
    assert delta["before"]["redacted"] is True
    assert "old-secret" not in protocol.ledger_path.read_text(encoding="utf-8")

    rows = protocol.ledger_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(rows[0])
    event["payload"]["title"] = "silently rewritten"
    protocol.ledger_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(ChangeProtocolError, match="hash mismatch"):
        protocol.verify()


@pytest.mark.asyncio
async def test_entity_tools_can_inspect_and_make_an_objection(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    proposal_id = _proposal(protocol).proposal_id
    registry = ToolRegistry()
    register_change_protocol_tools(registry, protocol)

    listed = json.loads(
        await registry.execute(ToolCallSpec(name="list_change_proposals", arguments={}))
    )
    assert listed["ok"] is True
    assert listed["proposals"][0]["proposal_id"] == proposal_id

    objected = json.loads(
        await registry.execute(
            ToolCallSpec(
                name="object_to_change",
                arguments={
                    "proposal_id": proposal_id,
                    "reason": "This alters a core trait I still endorse.",
                },
            )
        )
    )
    assert objected["ok"] is True
    assert objected["proposal"]["status"] == "objected"
    assert protocol.inspect(proposal_id).can_apply is False
