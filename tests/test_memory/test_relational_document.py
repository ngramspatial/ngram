"""Tests for prose-first relationship documents and helpers."""

from ngram.memory.relational_document import (
    bootstrap_document_from_relationship,
    trim_document_for_context,
    warmth_derived_to_relationship,
)
from ngram.models import Relationship


def test_trim_document_for_context_tail():
    long = "a " * 5000
    out = trim_document_for_context(long, max_chars=100)
    assert len(out) <= 110
    assert out.startswith("…")


def test_warmth_mapping():
    assert warmth_derived_to_relationship(0.0) == -1.0
    assert warmth_derived_to_relationship(0.5) == 0.0
    assert warmth_derived_to_relationship(1.0) == 1.0


def test_bootstrap_seeded_text():
    rel = Relationship(
        person_id="p1",
        name="Kai",
        first_met=1.0,
        last_interaction=2.0,
        interaction_count=5,
        familiarity=0.4,
        warmth=0.1,
        trust=0.5,
        dynamic="forming",
        notes=["likes infra"],
        topics_shared=["deploy"],
        unresolved=[],
    )
    text = bootstrap_document_from_relationship(rel)
    assert "Kai" in text
    assert "5 interactions" in text
    assert "deploy" in text or "infra" in text
