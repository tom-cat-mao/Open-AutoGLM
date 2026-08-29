"""v2 configuration layer: three-tier env / .env / CLI resolution.

Resolution order (highest wins): CLI overrides > shell env > project .env >
dataclass defaults. ``load_project_env()`` loads ``PHONE_AGENT_*`` keys from the
project ``.env`` without overriding values already present in the shell
environment (ported from the live-diagnosis ``run_diagnosis.py`` helper).

See ``docs/refactor-thin-loop-v2.md`` §4 for the binding contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# repo root = phone_agent/v2/config.py -> parents[2]
ROOT = Path(__file__).resolve().parents[2]


def load_project_env() -> None:
    """Load PHONE_AGENT_* defaults from the project .env without overriding shell values.

    Tolerates a leading ``export `` prefix and surrounding single/double quotes.
    Only keys with the ``PHONE_AGENT_`` prefix are loaded, and existing shell env
    values are never overwritten (shell env > .env).
    """

    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith("PHONE_AGENT_") or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _env_str(key: str, default: str) -> str:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw


def _env_opt_str(key: str) -> str | None:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return None
    return raw


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a float, got {raw!r}") from exc


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an int, got {raw!r}") from exc


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_choice(key: str, default: str, choices: tuple[str, ...]) -> str:
    """Read an enum-like value; illegal / empty values fall back to ``default``.

    Mirrors the design intent (S2 附A): a mistyped ``PHONE_AGENT_FINISH_VERIFY``
    must never crash bring-up — it silently degrades to the default mode.
    """

    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    return value if value in choices else default


def _env_bool_default_true(key: str, default: bool = True) -> bool:
    """Boolean flag that defaults to ``True`` and only ``0/false/no/off`` disable it.

    Used for opt-out switches (e.g. ``PHONE_AGENT_TASKDOC``) where any value other
    than an explicit falsy token keeps the feature enabled.
    """

    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _parse_sampling() -> dict[str, float]:
    """Parse optional sampling params; illegal float values raise ValueError."""

    sampling: dict[str, float] = {}
    for env_key, param in (
        ("PHONE_AGENT_TEMPERATURE", "temperature"),
        ("PHONE_AGENT_TOP_P", "top_p"),
        ("PHONE_AGENT_FREQUENCY_PENALTY", "frequency_penalty"),
    ):
        raw = os.getenv(env_key)
        if raw is None or not raw.strip():
            continue
        try:
            sampling[param] = float(raw)
        except ValueError as exc:
            raise ValueError(f"{env_key} must be a float, got {raw!r}") from exc
    return sampling


def _parse_cf_access() -> tuple[str | None, str | None]:
    """CF Access id/secret must be supplied as a pair or not at all."""

    cf_id = _env_opt_str("PHONE_AGENT_CF_ACCESS_CLIENT_ID")
    cf_secret = _env_opt_str("PHONE_AGENT_CF_ACCESS_CLIENT_SECRET")
    if bool(cf_id) != bool(cf_secret):
        raise ValueError(
            "PHONE_AGENT_CF_ACCESS_CLIENT_ID and PHONE_AGENT_CF_ACCESS_CLIENT_SECRET "
            "must be set together (a CF Access id/secret pair)"
        )
    return cf_id, cf_secret


@dataclass
class V2Config:
    """Resolved runtime configuration for one v2 run."""

    # model
    base_url: str
    model_name: str
    api_key: str = "EMPTY"
    model_timeout: float = 180.0
    model_max_retries: int = 2
    # device
    device_id: str | None = None
    # local App-KB (device labels + persistent aliases). PhoneSession opens the
    # store lazily so disabling it performs no filesystem writes.
    memory_dir: str = "memory"
    app_kb_enabled: bool = True
    app_list_max: int = 40
    dream_mode: str = "manual"
    # loop
    max_model_calls: int = 100
    # HITL resume budget (S1 §3.3): outer-loop cap on human-in-the-loop resumes,
    # orthogonal to the per-invoke model-call budget. Exhaustion ends the run with
    # reason ``hitl_resume_exhausted``.
    max_hitl_resumes: int = 20
    # L0 budget warn ratio (S1 §3.1): retained for backward compatibility. A4
    # re-based the budget from model-call count to token cost (see token_budget /
    # token_warn_remaining below); this field is no longer read by BudgetMiddleware.
    budget_warn_ratio: float = 0.8
    # L0 token budget (A4 §2): total token cost budget (input+output, summed from
    # usage_metadata). BudgetMiddleware injects a one-time remaining-token mirror
    # once the remaining budget drops to ``token_warn_remaining``. max_model_calls
    # is now a runaway-loop fuse only (ModelCallLimit), not the cost ceiling.
    token_budget: int = 1_000_000
    token_warn_remaining: int = 100_000
    # two-threshold auto-compact (A4 §3). ``compact_enabled`` is the master switch
    # (env ``PHONE_AGENT_COMPACT``, default on). Ratios are of the context window:
    # a T1 warn SystemMessage at ``compact_warn_ratio`` and a T2 forced handoff
    # summary at ``compact_trigger_ratio``. ``context_window`` overrides the
    # model-name inference (default 256k).
    compact_enabled: bool = True
    compact_warn_ratio: float = 0.75
    compact_trigger_ratio: float = 0.92
    context_window: int | None = None
    # context hygiene (S1 §1.4/§2): rolling image + OBS-marks pruning windows
    image_keep: int = 2
    obs_marks_keep: int = 2
    # grounding
    grounding_provider: str = "hybrid"
    accessibility_timeout: float = 3.0
    accessibility_max_marks: int = 80
    locateanything_model: str | None = None
    locateanything_max_size: int = 960
    # tool-call concurrency (U1): the thin loop is one-observation-one-action, so
    # parallel tool calls are disabled — a batch of calls issued against a single
    # observation would act on marks that a mid-batch action already invalidated
    # (the batch-badge freshness gate would then reject the later calls anyway).
    # False (default) forwards ``parallel_tool_calls=False`` to the gateway via
    # model_kwargs. Set True only if a gateway rejects the param.
    parallel_tool_calls: bool = False
    # i18n / misc
    lang: str = "cn"
    # trace
    trace_dir: str = ".traces"
    trace_enabled: bool = True
    # taskdoc (task board increment)
    taskdoc_enabled: bool = True
    taskdoc_nudge_steps: int = 5
    # finish verification (S2 §1.6/§4): off|auto|always. ``off`` degrades finish to
    # the pre-two-step single-call behavior; ``auto`` runs the independent-context
    # verifier only on trigger (high-risk goal / hard-contradiction confirm);
    # ``always`` verifies every confirm.
    finish_verify: str = "auto"
    # number of trailing screenshots handed to the finish verifier (S2 §4.2).
    finish_verify_k: int = 1
    # safety mode (U2): off|wary|hard|reviewer. ``wary`` (default) is the warning
    # system — a risky execution call (tap/long_press/type_text/launch_app) is NOT
    # executed and NOT human-interrupted; the tool returns a warning (world fact +
    # option space) and the model must resend with ``confirm_irreversible=true`` to
    # act. ``hard`` keeps the legacy HITL interrupt (approve/reject) for unattended
    # runs. ``off`` disables the gate. ``reviewer`` is ``wary`` plus second-model
    # precision-ranking of soft candidates (§3.3; semantics unchanged from S2).
    safety_mode: str = "wary"
    # diagnostic evidence stream (opt-in; default OFF, zero-cost when off).
    # Enabled by the live-diagnosis skill to emit full-text (bounded) run
    # evidence to ``<diagnostic_evidence_dir>/<run_id>.evidence.jsonl``.
    diagnostic_evidence: bool = False
    diagnostic_evidence_dir: str = "outputs/live-diagnosis/.evidence"
    # diagnostic full-fidelity mode (local-first). The diagnosis report's reader is
    # the device owner on their own machine, so when this is set the diagnostic
    # evidence stream keeps sensitive substrings UNREDACTED and text UNTRUNCATED
    # (still multimodal text/image split, still no base64 in the JSONL). Set by the
    # live-diagnosis skill in diagnosis mode; redaction only returns for an explicit
    # ``--share`` copy. This NEVER affects the P0 #6 production trace (trace.py).
    diagnostic_unredacted: bool = False
    # sampling params (temperature/top_p/frequency_penalty) forwarded to the model
    sampling: dict[str, float] | None = None
    # request headers extras
    user_agent: str | None = None
    http_headers: dict[str, str] | None = None
    cf_access_client_id: str | None = None
    cf_access_client_secret: str | None = None
    # reserved (read-only this round; not implemented)
    memory_model: str | None = None
    # finish-verifier model (S2 §4.3); falls back to the main model when unset.
    verifier_model: str | None = None
    # safety-reviewer model (S2 §3.3); falls back to verifier_model then the main
    # model when unset.
    safety_reviewer_model: str | None = None

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "V2Config":
        """Build a V2Config from shell/.env values, applying CLI overrides last.

        ``overrides`` mirrors the dataclass field names; ``None`` values are
        ignored so CLI flags left unset never clobber env-derived values.
        """

        sampling = _parse_sampling()
        cf_id, cf_secret = _parse_cf_access()

        http_headers: dict[str, str] = {}
        raw_headers = os.getenv("PHONE_AGENT_HTTP_HEADERS")
        if raw_headers:
            for pair in raw_headers.split(";"):
                if "=" in pair:
                    hkey, hvalue = pair.split("=", 1)
                    http_headers[hkey.strip()] = hvalue.strip()

        config = cls(
            base_url=_env_str("PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"),
            model_name=_env_str("PHONE_AGENT_MODEL", "autoglm-phone-9b"),
            api_key=_env_str("PHONE_AGENT_API_KEY", "EMPTY"),
            model_timeout=_env_float("PHONE_AGENT_MODEL_TIMEOUT", 180.0),
            model_max_retries=_env_int("PHONE_AGENT_MODEL_MAX_RETRIES", 2),
            device_id=_env_opt_str("PHONE_AGENT_DEVICE_ID"),
            memory_dir=_env_str("PHONE_AGENT_MEMORY_DIR", "memory"),
            app_kb_enabled=_env_bool_default_true("PHONE_AGENT_APP_KB", True),
            app_list_max=_env_int("PHONE_AGENT_APP_LIST_MAX", 40),
            dream_mode=_env_choice(
                "PHONE_AGENT_DREAM", "manual", ("off", "auto", "manual")
            ),
            max_model_calls=_env_int("PHONE_AGENT_MAX_STEPS", 100),
            max_hitl_resumes=_env_int("PHONE_AGENT_MAX_HITL_RESUMES", 20),
            budget_warn_ratio=_env_float("PHONE_AGENT_BUDGET_WARN_RATIO", 0.8),
            token_budget=_env_int("PHONE_AGENT_TOKEN_BUDGET", 1_000_000),
            token_warn_remaining=_env_int(
                "PHONE_AGENT_TOKEN_WARN_REMAINING", 100_000
            ),
            compact_enabled=_env_bool_default_true("PHONE_AGENT_COMPACT", True),
            compact_warn_ratio=_env_float("PHONE_AGENT_COMPACT_WARN_RATIO", 0.75),
            compact_trigger_ratio=_env_float(
                "PHONE_AGENT_COMPACT_TRIGGER_RATIO", 0.92
            ),
            context_window=(
                _env_int("PHONE_AGENT_CONTEXT_WINDOW", 0) or None
            ),
            image_keep=_env_int("PHONE_AGENT_IMAGE_KEEP", 2),
            obs_marks_keep=_env_int("PHONE_AGENT_OBS_MARKS_KEEP", 2),
            grounding_provider=_env_str("PHONE_AGENT_GROUNDING_PROVIDER", "hybrid"),
            accessibility_timeout=_env_float("PHONE_AGENT_ACCESSIBILITY_TIMEOUT", 3.0),
            accessibility_max_marks=_env_int("PHONE_AGENT_ACCESSIBILITY_MAX_MARKS", 80),
            locateanything_model=_env_opt_str("PHONE_AGENT_LOCATEANYTHING_MODEL"),
            locateanything_max_size=_env_int("PHONE_AGENT_LOCATEANYTHING_MAX_SIZE", 960),
            parallel_tool_calls=_env_bool("PHONE_AGENT_PARALLEL_TOOL_CALLS", False),
            lang=_env_str("PHONE_AGENT_LANG", "cn"),
            trace_dir=_env_str("PHONE_AGENT_TRACE_DIR", ".traces"),
            trace_enabled=_env_bool("PHONE_AGENT_TRACE", True),
            taskdoc_enabled=_env_bool_default_true("PHONE_AGENT_TASKDOC", True),
            taskdoc_nudge_steps=_env_int("PHONE_AGENT_TASKDOC_NUDGE_STEPS", 5),
            finish_verify=_env_choice(
                "PHONE_AGENT_FINISH_VERIFY", "auto", ("off", "auto", "always")
            ),
            finish_verify_k=_env_int("PHONE_AGENT_FINISH_VERIFY_K", 1),
            safety_mode=_env_choice(
                "PHONE_AGENT_SAFETY_MODE", "wary", ("off", "wary", "hard", "reviewer")
            ),
            diagnostic_evidence=_env_bool("PHONE_AGENT_DIAG_EVIDENCE", False),
            diagnostic_evidence_dir=_env_str(
                "PHONE_AGENT_DIAG_EVIDENCE_DIR", "outputs/live-diagnosis/.evidence"
            ),
            diagnostic_unredacted=_env_bool("PHONE_AGENT_DIAG_UNREDACTED", False),
            sampling=sampling or None,
            user_agent=_env_opt_str("PHONE_AGENT_USER_AGENT"),
            http_headers=http_headers or None,
            cf_access_client_id=cf_id,
            cf_access_client_secret=cf_secret,
            memory_model=_env_opt_str("PHONE_AGENT_MEMORY_MODEL"),
            verifier_model=_env_opt_str("PHONE_AGENT_VERIFIER_MODEL"),
            safety_reviewer_model=_env_opt_str("PHONE_AGENT_SAFETY_REVIEWER_MODEL"),
        )

        for field_name, value in (overrides or {}).items():
            if value is None:
                continue
            if not hasattr(config, field_name):
                raise ValueError(f"Unknown V2Config override: {field_name}")
            setattr(config, field_name, value)

        return config
