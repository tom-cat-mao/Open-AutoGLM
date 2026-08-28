"""Tests for v2 config resolution: env/.env/override priority, sampling, CF pair, headers."""

from __future__ import annotations

import pytest

from phone_agent.v2 import config as config_mod
from phone_agent.v2.config import V2Config, load_project_env
from phone_agent.v2.model import DEFAULT_MODEL_USER_AGENT, build_default_headers

PHONE_AGENT_KEYS = [
    "PHONE_AGENT_BASE_URL",
    "PHONE_AGENT_MODEL",
    "PHONE_AGENT_API_KEY",
    "PHONE_AGENT_MODEL_TIMEOUT",
    "PHONE_AGENT_MODEL_MAX_RETRIES",
    "PHONE_AGENT_DEVICE_ID",
    "PHONE_AGENT_MAX_STEPS",
    "PHONE_AGENT_MAX_HITL_RESUMES",
    "PHONE_AGENT_BUDGET_WARN_RATIO",
    "PHONE_AGENT_IMAGE_KEEP",
    "PHONE_AGENT_OBS_MARKS_KEEP",
    "PHONE_AGENT_GROUNDING_PROVIDER",
    "PHONE_AGENT_ACCESSIBILITY_TIMEOUT",
    "PHONE_AGENT_ACCESSIBILITY_MAX_MARKS",
    "PHONE_AGENT_LOCATEANYTHING_MODEL",
    "PHONE_AGENT_LOCATEANYTHING_MAX_SIZE",
    "PHONE_AGENT_LANG",
    "PHONE_AGENT_TRACE_DIR",
    "PHONE_AGENT_TRACE",
    "PHONE_AGENT_FINISH_VERIFY",
    "PHONE_AGENT_TEMPERATURE",
    "PHONE_AGENT_TOP_P",
    "PHONE_AGENT_FREQUENCY_PENALTY",
    "PHONE_AGENT_USER_AGENT",
    "PHONE_AGENT_HTTP_HEADERS",
    "PHONE_AGENT_CF_ACCESS_CLIENT_ID",
    "PHONE_AGENT_CF_ACCESS_CLIENT_SECRET",
    "PHONE_AGENT_MEMORY_MODEL",
    "PHONE_AGENT_VERIFIER_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test with all PHONE_AGENT_* keys unset."""

    for key in PHONE_AGENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# -- defaults ------------------------------------------------------------


def test_from_env_defaults():
    cfg = V2Config.from_env()
    assert cfg.api_key == "EMPTY"
    assert cfg.model_timeout == 180.0
    assert cfg.model_max_retries == 2
    assert cfg.max_model_calls == 20
    assert cfg.max_hitl_resumes == 20
    assert cfg.budget_warn_ratio == 0.8
    assert cfg.finish_verify == "auto"
    assert cfg.image_keep == 2
    assert cfg.obs_marks_keep == 2
    assert cfg.grounding_provider == "hybrid"
    assert cfg.accessibility_timeout == 3.0
    assert cfg.accessibility_max_marks == 80
    assert cfg.locateanything_max_size == 960
    assert cfg.lang == "cn"
    assert cfg.trace_dir == ".traces"
    assert cfg.trace_enabled is True
    assert cfg.device_id is None
    assert cfg.sampling is None


def test_from_env_reads_shell_env(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("PHONE_AGENT_MODEL", "kimi-x")
    monkeypatch.setenv("PHONE_AGENT_MAX_STEPS", "7")
    monkeypatch.setenv("PHONE_AGENT_DEVICE_ID", "SERIAL123")
    monkeypatch.setenv("PHONE_AGENT_TRACE", "false")
    cfg = V2Config.from_env()
    assert cfg.base_url == "https://gw.example/v1"
    assert cfg.model_name == "kimi-x"
    assert cfg.max_model_calls == 7
    assert cfg.device_id == "SERIAL123"
    assert cfg.trace_enabled is False


def test_context_pruning_keys_env_and_override(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_IMAGE_KEEP", "3")
    monkeypatch.setenv("PHONE_AGENT_OBS_MARKS_KEEP", "4")
    cfg = V2Config.from_env()
    assert cfg.image_keep == 3
    assert cfg.obs_marks_keep == 4
    # CLI override beats env.
    cfg2 = V2Config.from_env({"image_keep": 1, "obs_marks_keep": 5})
    assert cfg2.image_keep == 1
    assert cfg2.obs_marks_keep == 5


def test_budget_and_resume_keys_env_and_override(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_MAX_HITL_RESUMES", "7")
    monkeypatch.setenv("PHONE_AGENT_BUDGET_WARN_RATIO", "0.5")
    cfg = V2Config.from_env()
    assert cfg.max_hitl_resumes == 7
    assert cfg.budget_warn_ratio == 0.5
    # CLI override beats env.
    cfg2 = V2Config.from_env({"max_hitl_resumes": 3, "budget_warn_ratio": 0.9})
    assert cfg2.max_hitl_resumes == 3
    assert cfg2.budget_warn_ratio == 0.9


@pytest.mark.parametrize("raw", ["off", "auto", "always", "ALWAYS", "Off"])
def test_finish_verify_valid_values(monkeypatch, raw):
    monkeypatch.setenv("PHONE_AGENT_FINISH_VERIFY", raw)
    assert V2Config.from_env().finish_verify == raw.strip().lower()


@pytest.mark.parametrize("raw", ["", "  ", "bogus", "yes", "1"])
def test_finish_verify_illegal_falls_back_to_auto(monkeypatch, raw):
    monkeypatch.setenv("PHONE_AGENT_FINISH_VERIFY", raw)
    assert V2Config.from_env().finish_verify == "auto"


# -- priority: override > shell env > .env > default ---------------------


def test_override_beats_env(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_MODEL", "env-model")
    monkeypatch.setenv("PHONE_AGENT_MAX_STEPS", "5")
    cfg = V2Config.from_env({"model_name": "cli-model", "max_model_calls": 99})
    assert cfg.model_name == "cli-model"
    assert cfg.max_model_calls == 99


def test_none_override_does_not_clobber(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_MODEL", "env-model")
    cfg = V2Config.from_env({"model_name": None, "device_id": None})
    assert cfg.model_name == "env-model"


def test_unknown_override_raises():
    with pytest.raises(ValueError):
        V2Config.from_env({"not_a_field": "x"})


def test_env_beats_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'export PHONE_AGENT_MODEL="dotenv-model"\n'
        "PHONE_AGENT_LANG='en'\n"
        "# comment line\n"
        "PHONE_AGENT_MAX_STEPS=15\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "ROOT", tmp_path)
    # shell env already sets MODEL -> .env must NOT override it
    monkeypatch.setenv("PHONE_AGENT_MODEL", "shell-model")
    load_project_env()
    assert __import__("os").environ["PHONE_AGENT_MODEL"] == "shell-model"
    # keys not in shell env come from .env, with quotes/export stripped
    assert __import__("os").environ["PHONE_AGENT_LANG"] == "en"
    assert __import__("os").environ["PHONE_AGENT_MAX_STEPS"] == "15"
    cfg = V2Config.from_env()
    assert cfg.model_name == "shell-model"
    assert cfg.lang == "en"
    assert cfg.max_model_calls == 15


def test_dotenv_missing_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "ROOT", tmp_path / "nope")
    load_project_env()  # must not raise


# -- sampling ------------------------------------------------------------


def test_sampling_parsing(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_TEMPERATURE", "1.0")
    monkeypatch.setenv("PHONE_AGENT_TOP_P", "0.95")
    monkeypatch.setenv("PHONE_AGENT_FREQUENCY_PENALTY", "0")
    cfg = V2Config.from_env()
    assert cfg.sampling == {"temperature": 1.0, "top_p": 0.95, "frequency_penalty": 0.0}


def test_sampling_partial(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_TEMPERATURE", "0.7")
    cfg = V2Config.from_env()
    assert cfg.sampling == {"temperature": 0.7}


def test_sampling_bad_float_raises(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_TEMPERATURE", "hot")
    with pytest.raises(ValueError):
        V2Config.from_env()


def test_bad_float_timeout_raises(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_MODEL_TIMEOUT", "soon")
    with pytest.raises(ValueError):
        V2Config.from_env()


# -- CF Access pair validation -------------------------------------------


def test_cf_access_pair_ok(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID", "cid")
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET", "csecret")
    cfg = V2Config.from_env()
    assert cfg.cf_access_client_id == "cid"
    assert cfg.cf_access_client_secret == "csecret"


def test_cf_access_id_only_raises(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID", "cid")
    with pytest.raises(ValueError):
        V2Config.from_env()


def test_cf_access_secret_only_raises(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET", "csecret")
    with pytest.raises(ValueError):
        V2Config.from_env()


# -- headers -------------------------------------------------------------


def test_headers_default_ua(monkeypatch):
    cfg = V2Config.from_env()
    headers = build_default_headers(cfg)
    assert headers["User-Agent"] == DEFAULT_MODEL_USER_AGENT
    assert "CF-Access-Client-Id" not in headers


def test_headers_custom_ua_and_http_headers(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_USER_AGENT", "MyUA/1.0")
    monkeypatch.setenv("PHONE_AGENT_HTTP_HEADERS", "X-A=1;X-B=2")
    cfg = V2Config.from_env()
    headers = build_default_headers(cfg)
    assert headers["User-Agent"] == "MyUA/1.0"
    assert headers["X-A"] == "1"
    assert headers["X-B"] == "2"


def test_headers_http_headers_do_not_override_explicit_ua(monkeypatch):
    # A UA supplied via HTTP_HEADERS takes precedence over the default constant
    monkeypatch.setenv("PHONE_AGENT_HTTP_HEADERS", "User-Agent=FromHeaders/9")
    cfg = V2Config.from_env()
    headers = build_default_headers(cfg)
    assert headers["User-Agent"] == "FromHeaders/9"


def test_headers_with_cf_access(monkeypatch):
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_ID", "cid")
    monkeypatch.setenv("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET", "csecret")
    cfg = V2Config.from_env()
    headers = build_default_headers(cfg)
    assert headers["CF-Access-Client-Id"] == "cid"
    assert headers["CF-Access-Client-Secret"] == "csecret"
