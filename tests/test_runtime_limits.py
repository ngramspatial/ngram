from ngram.cognition.deliberate import DeliberateCognition
from ngram.config import HarnessConfig, load_entity_config
import ngram.config as config_module


def test_local_limits_overlay_is_scoped_to_its_entity(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "project_configs_dir", lambda: tmp_path)
    entities = tmp_path / "entities"
    entities.mkdir()
    rook = entities / "rook.yaml"
    rook.write_text("name: Rook\ncognition:\n  tool_continuation_rounds: 12\n")
    (entities / "rook.local.yaml").write_text(
        "cognition:\n  tool_continuation_rounds: 9994\n"
        "  message_budget_per_turn: 1000\n  turn_timeout_seconds: 86400\n"
        "  deliberate_max_tokens: null\n"
        "tools:\n  shell:\n    timeout: 1800\n"
    )
    other = entities / "other.yaml"
    other.write_text("name: Other\ncognition:\n  tool_continuation_rounds: 12\n")
    harness = HarnessConfig()
    config = load_entity_config("rook", harness)
    assert config.name == "Rook"
    assert DeliberateCognition(config, object())._agent_step_cap() == 10000
    assert config.cognition.message_budget_per_turn == 1000
    assert config.cognition.turn_timeout_seconds == 86400
    assert config.cognition.deliberate_max_tokens is None
    assert DeliberateCognition(config, object())._inference_params("deliberate")[1] is None
    assert config.raw["tools"]["shell"]["timeout"] == 1800
    ordinary = load_entity_config("other", harness)
    assert DeliberateCognition(ordinary, object())._agent_step_cap() == 18
    assert ordinary.cognition.turn_timeout_seconds == 120
    assert ordinary.cognition.message_budget_per_turn == 12
    assert "tools" not in ordinary.raw
    assert DeliberateCognition(ordinary, object())._inference_params("deliberate")[1] == harness.cognition.deliberate_max_tokens
