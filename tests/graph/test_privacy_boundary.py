from phone_agent.graph.marks import MarkRegistry
from phone_agent.grounding.accessibility import parse_uiautomator_marks
from phone_agent.graph.nodes.observation_capture import state_before_observation_payload
from phone_agent.graph.verifier import _observation_text


def test_state_observation_keeps_regex_cleaned_text_for_verifier() -> None:
    registry = MarkRegistry.from_marks(
        "screen-1",
        [
            {
                "mark_id": "ax_1",
                "bbox": [0, 0, 100, 100],
                "center": [50, 50],
                "text_summary": "银石赛道 F1 赛记",
            }
        ],
    )
    state = {
        "observation": {
            "snapshot": {"screen_id": "screen-1"},
            "mark_registry": registry.to_dict(),
        }
    }

    before_observation = state_before_observation_payload(state)

    assert _observation_text(before_observation) == "银石赛道 f1 赛记"
    assert registry.trace_summary()["marks"][0]["text_summary"] == {
        "redacted": True,
        "length": 10,
    }


def test_password_text_is_none_at_every_mark_boundary() -> None:
    marks = parse_uiautomator_marks(
        '<hierarchy><node text="secret" password="true" '
        'class="android.widget.EditText" focusable="true" '
        'bounds="[0,0][100,100]" /></hierarchy>',
        screen_width=100,
        screen_height=100,
    )
    registry = MarkRegistry.from_marks("screen-1", marks)
    mark = registry.marks["ax_1"]

    assert mark.text_summary is None
    assert mark.to_prompt_dict()["text_summary"] is None
    assert mark.to_trace_dict()["text_summary"] is None
