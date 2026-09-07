from types import SimpleNamespace

from click.testing import CliRunner

from ngram import main as main_mod


class _FakeEntity:
    def __init__(self, config):
        self.config = config
        self.perceived = []
        self.registered = []
        self.shutdown_called = False

    def register_platform(self, name, platform) -> None:
        self.registered.append((name, platform))

    async def perceive(self, inp, *, reply_platform=None, **kwargs):
        self.perceived.append((inp, reply_platform, kwargs))
        return ("canary says hi", False)

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_ask_command_prints_single_reply(monkeypatch) -> None:
    runner = CliRunner()
    prepare_calls = []
    shutdown_calls = []
    entity_box = {}
    fake_harness = object()
    fake_config = SimpleNamespace(
        name="Canary",
        harness=SimpleNamespace(logging=SimpleNamespace(level="INFO", format="json")),
        log_path=lambda: "ignored.log",
    )

    monkeypatch.setattr(main_mod, "_load_repo_dotenv", lambda: None)
    monkeypatch.setattr(
        main_mod,
        "_prepare_ollama_cli",
        lambda entity_name, with_ollama, pull_models: prepare_calls.append(
            (entity_name, with_ollama, pull_models)
        ),
    )
    monkeypatch.setattr(main_mod, "load_harness_config", lambda: fake_harness)
    monkeypatch.setattr(
        main_mod,
        "load_entity_config",
        lambda entity_name, harness: fake_config if (entity_name, harness) == ("canary", fake_harness) else None,
    )
    monkeypatch.setattr(main_mod, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod, "shutdown_spawned_ollama", lambda: shutdown_calls.append(True))

    def _fake_entity_factory(config):
        ent = _FakeEntity(config)
        entity_box["entity"] = ent
        return ent

    monkeypatch.setattr(main_mod, "Entity", _fake_entity_factory)

    result = runner.invoke(main_mod.cli, ["ask", "canary", "hey", "there"])

    assert result.exit_code == 0
    assert result.output == "canary says hi\n"
    assert prepare_calls == [("canary", False, False)]
    assert shutdown_calls == [True]

    ent = entity_box["entity"]
    assert ent.shutdown_called is True
    assert len(ent.registered) == 1
    assert ent.registered[0][0] == "cli"

    inp, reply_platform, kwargs = ent.perceived[0]
    assert inp.text == "hey there"
    assert inp.person_id == "cli_user"
    assert inp.person_name == "You"
    assert inp.channel == "cli"
    assert inp.platform == "cli"
    assert reply_platform is ent.registered[0][1]
    assert kwargs == {}
