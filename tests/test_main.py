import importlib.util
from pathlib import Path


def _load_main_module():
    module_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("open_autoglm_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_redact_url_for_display_hides_credentials_and_query_secrets() -> None:
    main = _load_main_module()

    redacted = main.redact_url_for_display(
        "https://user:pass@example.com/v1?api_key=secret&token=abc&safe=1"
    )

    assert redacted == "https://<redacted>@example.com/v1?api_key=<redacted>&token=<redacted>&safe=1"
    assert "user" not in redacted
    assert "secret" not in redacted
    assert "abc" not in redacted


def test_check_model_api_redacts_base_url_on_error(monkeypatch, capsys) -> None:
    main = _load_main_module()

    class BrokenCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("Connection error for https://user:pass@example.com/v1?api_key=secret")

    class BrokenChat:
        completions = BrokenCompletions()

    class BrokenClient:
        def __init__(self, **_kwargs):
            self.chat = BrokenChat()

    monkeypatch.setattr(main, "OpenAI", BrokenClient)

    assert main.check_model_api(
        "https://user:pass@example.com/v1?api_key=secret", "model", api_key="sk-secret"
    ) is False
    output = capsys.readouterr().out

    assert "https://<redacted>@example.com/v1?api_key=<redacted>" in output
    assert "user:pass" not in output
    assert "api_key=secret" not in output
    assert "sk-secret" not in output


def test_main_unloads_models_after_run_in_finally() -> None:
    """P2: main.py releases the loaded MLX model when a run ends, on both the
    single-task path and the interactive loop, inside ``finally`` so exception
    paths release too."""
    main = _load_main_module()
    source = Path(main.__file__).read_text(encoding="utf-8")

    assert "finally:" in source
    assert source.count("agent.unload_models()") >= 2
    assert "agent.unload_models()" in source.split("finally:")[1]
