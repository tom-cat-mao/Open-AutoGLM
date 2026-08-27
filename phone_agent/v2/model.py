"""v2 model layer: ChatOpenAI factory + default request headers.

Mirrors the verified ``scripts/spike_langchain_compat.py`` setup: an
OpenAI-compatible gateway reached through ``langchain_openai.ChatOpenAI`` with
browser-style UA + optional CF Access headers and sampling params forwarded from
:class:`~phone_agent.v2.config.V2Config`.

See ``docs/refactor-thin-loop-v2.md`` §5 for the binding contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from phone_agent.v2.config import V2Config


DEFAULT_MODEL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Open-AutoGLM/0.1"
)


def build_default_headers(config: "V2Config") -> dict[str, str]:
    """Build gateway request headers: UA + custom headers + CF Access pair.

    Custom ``http_headers`` are applied first; ``User-Agent`` falls back to the
    configured value or the browser-style default constant. The CF Access pair is
    only emitted when both id and secret are present (config validated the pairing).
    """

    headers: dict[str, str] = {}
    if config.http_headers:
        headers.update(config.http_headers)
    headers.setdefault("User-Agent", config.user_agent or DEFAULT_MODEL_USER_AGENT)
    if config.cf_access_client_id and config.cf_access_client_secret:
        headers["CF-Access-Client-Id"] = config.cf_access_client_id
        headers["CF-Access-Client-Secret"] = config.cf_access_client_secret
    return headers


def build_chat_model(config: "V2Config") -> "BaseChatModel":
    """Construct the ChatOpenAI client for the configured gateway.

    Sampling params (temperature/top_p/frequency_penalty) are forwarded as-is;
    the gateway + tool_calls + image_url content blocks are all verified compatible
    by the langchain compat spike.
    """

    from langchain_openai import ChatOpenAI

    sampling = dict(config.sampling or {})
    return ChatOpenAI(
        base_url=config.base_url,
        model=config.model_name,
        api_key=config.api_key,
        timeout=config.model_timeout,
        max_retries=config.model_max_retries,
        default_headers=build_default_headers(config),
        **sampling,
    )
