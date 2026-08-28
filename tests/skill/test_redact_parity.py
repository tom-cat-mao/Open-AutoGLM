"""Redaction parity test (§5.6): trace (truncated) vs diagnostic (full-text).

The two egress consumers share one base64-drop / sensitive-redaction primitive
(:mod:`phone_agent.v2.middleware._redact`). This asserts the shared guarantees
hold identically for both, and that the *only* difference is the length policy:

* ``TraceMiddleware`` (P0 #6) caps every string at 64 chars (``…`` suffix);
* ``DiagnosticEvidenceMiddleware`` keeps the full redacted string (bounded at
  ``DIAG_MAX_TEXT``).

Both must drop screenshot base64 and redact sensitive substrings; they must
differ only in the length of the retained text.
"""

from __future__ import annotations

from phone_agent.v2.middleware._redact import (
    redact_text,
    redact_value_no_base64,
)
from phone_agent.v2.middleware.diagnostic import DIAG_MAX_TEXT, _bounded_text
from phone_agent.v2.middleware.trace import _redact_text as trace_redact_text
from phone_agent.v2.middleware.trace import redact_args

FAKE_PHONE = "13800138000"
FAKE_KEY = "sk-abcDEF0123456789xyz"
B64 = "QUJD" * 100  # 400 chars > redact's 120-char base64 threshold


def test_both_redact_sensitive_and_differ_only_in_length():
    # A string comfortably longer than 64 chars carrying a phone number, so the
    # 64-char trace cap is strictly shorter than the full diagnostic text.
    text = (
        f"用户在登录页输入了手机号 {FAKE_PHONE} 并点击了下一步按钮，"
        f"随后系统提示需要短信验证码校验，这段中文说明特意写得足够长，"
        f"明显超过六十四个字符的上限，以便让截断策略与全文策略产生可观察的长度差异。"
    )

    trace_out = trace_redact_text(text)
    diag_out = _bounded_text(text)  # str when within DIAG_MAX_TEXT

    # neither leaks the phone number.
    assert FAKE_PHONE not in trace_out
    assert FAKE_PHONE not in diag_out
    assert "<redacted>" in trace_out
    assert "<redacted>" in diag_out

    # trace truncates to 64 chars (+ the … suffix); diagnostic keeps it all.
    assert len(trace_out) <= 65
    assert trace_out.endswith("…")
    assert isinstance(diag_out, str)
    assert len(diag_out) > len(trace_out)
    # the diagnostic text is exactly the uncapped redaction.
    assert diag_out == redact_text(text)


def test_both_drop_base64_in_image_blocks():
    image_block = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{B64}"},
        "screen_seq": 7,
    }

    trace_val = redact_value_no_base64(image_block, trace_redact_text)
    diag_val = redact_value_no_base64(image_block, redact_text)

    for val in (trace_val, diag_val):
        assert val["type"] == "image"
        assert val["screen_seq"] == 7
        assert isinstance(val["bytes"], int) and val["bytes"] > 0
        # the base64 payload is gone in both.
        assert B64 not in str(val)
        assert "data:image" not in str(val)


def test_args_parity_for_multimodal_tool_return():
    # A tool return of [text block + image block]: both consumers keep the text
    # (redacted) and drop the image base64.
    content = [
        {"type": "text", "text": f"OK. typed 密钥 {FAKE_KEY}"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{B64}"}, "screen_seq": 3},
    ]

    trace_val = redact_args(content)  # trace policy (truncating text_redactor)
    diag_val = redact_value_no_base64(content, redact_text)  # diagnostic policy

    for val in (trace_val, diag_val):
        # first block is redacted text; second is an image stub without base64.
        assert FAKE_KEY not in str(val)
        assert B64 not in str(val)
        assert val[1]["type"] == "image"
        assert val[1]["screen_seq"] == 3

    # text-length policy still differs: trace caps the text block, diagnostic does not.
    assert len(trace_val[0]["text"]) <= 65
    assert diag_val[0]["text"] == redact_text(content[0]["text"])
    assert "<redacted>" in diag_val[0]["text"]


def test_diag_bound_marks_overlong_text():
    # A string longer than DIAG_MAX_TEXT is bounded with a marker (still no base64,
    # still redacted), proving "diagnostic = full text but bounded", not unbounded.
    long_text = "安全内容 " * 2000  # well over DIAG_MAX_TEXT chars
    out = _bounded_text(long_text)
    assert isinstance(out, dict)
    assert out["_truncated"] is True
    assert out["_orig_len"] > DIAG_MAX_TEXT
    assert len(out["text"]) == DIAG_MAX_TEXT
