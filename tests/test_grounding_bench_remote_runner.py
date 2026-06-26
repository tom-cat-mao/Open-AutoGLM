import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from bench.grounding.reporting import enrich_prediction
from bench.grounding.run_remote_provider import main, run_case
from phone_agent.grounding.remote_openai import RemoteOpenAIGroundingProvider


def _case(tmp_path: Path) -> dict:
    image_path = tmp_path / "screen.png"
    Image.new("RGB", (100, 200), color="white").save(image_path)
    return {
        "id": "case-1",
        "image": str(image_path),
        "prompt": "search box 13800138000",
        "elements": [{"id": "target", "bbox": [100, 200, 300, 400], "required": True, "type": "input"}],
    }


def test_remote_runner_outputs_scoreable_prediction_without_raw_prompt(tmp_path: Path) -> None:
    case = _case(tmp_path)
    provider = RemoteOpenAIGroundingProvider(request_callable=lambda **kwargs: {"choices": [{"message": {"content": "<box>100 200 300 400</box>"}}]})

    prediction = run_case(provider, case, timeout=1)
    scored = enrich_prediction(prediction, case)
    raw = json.dumps(prediction, ensure_ascii=False)

    assert prediction["provider"] == "remote_openai"
    assert prediction["model"] == "step-3.7-flash"
    assert prediction["success"] is True
    assert prediction["bbox"] == [100, 200, 300, 400]
    assert prediction["prompt_hash"]
    assert "prompt" not in prediction
    assert "13800138000" not in raw
    assert scored["center_hit"] is True
    assert "api_key" not in raw


def test_remote_runner_main_fails_fast_without_api_key(tmp_path: Path, monkeypatch) -> None:
    case = _case(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv("MISSING_REMOTE_KEY", raising=False)

    try:
        main([
            "--manifest", str(manifest),
            "--output", str(tmp_path / "predictions.jsonl"),
            "--summary-output", str(tmp_path / "summary.json"),
            "--api-key-env", "MISSING_REMOTE_KEY",
        ])
    except SystemExit as exc:
        assert "missing API key env" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
