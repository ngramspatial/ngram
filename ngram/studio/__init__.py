"""Model-free local Studio for inspecting and maintaining open ngram containers."""

from ngram.studio.app import create_studio_app
from ngram.studio.factory_app import create_entity_factory_app

__all__ = ["create_entity_factory_app", "create_studio_app"]
