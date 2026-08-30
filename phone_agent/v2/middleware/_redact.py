"""Shared egress-redaction primitives for the v2 middleware layer.

Single source of truth for the two things every logged payload must guarantee
(P0 #6): sensitive substrings are stripped, and screenshot ``base64`` is never
written to disk. Both :mod:`phone_agent.v2.middleware.trace` (the P0-compliant
production trace, 64-char truncated) and
:mod:`phone_agent.v2.middleware.diagnostic` (the opt-in diagnosis evidence
stream, full-text-but-bounded) import from here so the base64-drop / sensitive-
redaction logic exists in exactly one place.

The *only* policy difference between the two consumers is the string-redaction
callable they pass to :func:`redact_value_no_base64`:

* trace caps every string at 64 chars (``…`` suffix) — see ``trace._redact_text``;
* diagnostic keeps the full redacted string, bounded at ``DIAG_MAX_TEXT``.

Neither can ever emit an image ``base64`` payload — that is enforced here,
below both callables.
"""

from __future__ import annotations

from typing import Any, Callable

from phone_agent.config.redact import redact_context_text


def redact_text(text: str) -> str:
    """Sensitive-substring redaction with no length cap (P0 #6 primitive).

    Thin alias over :func:`phone_agent.config.redact.redact_context_text`: phone
    numbers, emails, order/verification codes, api-keys, tokens, JWTs and long
    base64 runs are replaced with ``<redacted>``. Callers layer their own length
    policy (trace truncates to 64; diagnostic bounds to ``DIAG_MAX_TEXT``).
    """

    return redact_context_text(text)


def estimate_image_bytes(url: str) -> int:
    """Estimate raw byte length of a data: URL without logging its content."""

    if not url:
        return 0
    marker = "base64,"
    idx = url.find(marker)
    b64 = url[idx + len(marker) :] if idx != -1 else url
    # base64 encodes 3 bytes per 4 chars.
    return (len(b64) * 3) // 4


def _image_stub(value: dict) -> dict[str, Any]:
    """Reduce an image content block to ``{type, screen_seq, bytes}`` (no base64)."""

    payload = value.get("image_url")
    url = ""
    if isinstance(payload, dict):
        url = str(payload.get("url", ""))
    elif isinstance(payload, str):
        url = payload
    return {
        "type": "image",
        "screen_seq": value.get("screen_seq"),
        "bytes": estimate_image_bytes(url),
    }


def redact_value_no_base64(
    value: Any,
    text_redactor: Callable[[str], Any] = redact_text,
) -> Any:
    """Recursively redact a JSON-able value; drop image base64 payloads.

    * ``str`` -> ``text_redactor(str)`` (caller owns the length policy);
    * image content block (``type`` in ``{image_url, image}`` or an ``image_url``
      key) -> ``{type: "image", screen_seq, bytes}`` — the base64 is never kept;
    * ``dict`` / ``list`` / ``tuple`` -> recurse with the same ``text_redactor``;
    * anything else is returned unchanged.
    """

    if isinstance(value, str):
        return text_redactor(value)
    if isinstance(value, dict):
        btype = value.get("type")
        if btype in {"image_url", "image"} or "image_url" in value:
            return _image_stub(value)
        return {
            str(k): redact_value_no_base64(v, text_redactor) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value_no_base64(item, text_redactor) for item in value]
    return value


__all__ = ["redact_text", "estimate_image_bytes", "redact_value_no_base64"]
