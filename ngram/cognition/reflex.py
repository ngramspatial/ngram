"""Reflex routing (fast turns) still exists in ``CognitionRouter``.

Inference for reflex no longer uses a separate class: ``entity.perceive`` calls
``DeliberateCognition.iter_responses(..., inference_profile=\"reflex\")`` so reflex
turns get the same tool channel, inner voice, and delivery path as deliberate,
using the reflex model and ``reflex_max_tokens`` from harness config.
"""

__all__: list[str] = []
