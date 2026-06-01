import pytest

from phone_agent.actions.handler import parse_action


def test_parse_do_action_with_literal_values() -> None:
    action = parse_action('do(action="Tap", element=[123, 456])')

    assert action == {"_metadata": "do", "action": "Tap", "element": [123, 456]}


def test_parse_type_name_preserves_action_name_and_raw_text() -> None:
    action = parse_action('do(action="Type_Name", text="a,b(测试)\\n下一行")')

    assert action["action"] == "Type_Name"
    assert action["text"] == "a,b(测试)\\n下一行"


def test_parse_finish_uses_ast_literals() -> None:
    action = parse_action('finish(message="done, with ) parentheses")')

    assert action == {"_metadata": "finish", "message": "done, with ) parentheses"}


def test_parse_rejects_non_literal_code_execution() -> None:
    with pytest.raises(ValueError):
        parse_action('do(action=__import__("os").system("echo unsafe"))')


def test_parse_rejects_wrong_function_name() -> None:
    with pytest.raises(ValueError):
        parse_action('download(action="Tap", element=[1, 2])')
