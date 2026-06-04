import pytest

from phone_agent.actions.handler import parse_action


def test_parse_do_action_with_literal_values() -> None:
    action = parse_action('do(action="Tap", element=[123, 456])')

    assert action == {"_metadata": "do", "action": "Tap", "element": [123, 456]}


def test_parse_type_name_preserves_action_name_and_raw_text() -> None:
    action = parse_action('do(action="Type_Name", text="a,b(测试)\\n下一行")')

    assert action["action"] == "Type_Name"
    assert action["text"] == "a,b(测试)\n下一行"


def test_parse_type_uses_ast_literals_for_escaped_characters() -> None:
    action = parse_action('do(action="Type", text="quote: \\" slash: \\\\ paren: )")')

    assert action == {
        "_metadata": "do",
        "action": "Type",
        "text": 'quote: " slash: \\ paren: )',
    }


def test_parse_type_rejects_non_literal_text_value() -> None:
    with pytest.raises(ValueError):
        parse_action('do(action="Type", text=(lambda: "unsafe")())')


def test_parse_finish_uses_ast_literals() -> None:
    action = parse_action('finish(message="done, with ) parentheses")')

    assert action == {"_metadata": "finish", "message": "done, with ) parentheses"}


def test_parse_rejects_non_literal_code_execution() -> None:
    with pytest.raises(ValueError):
        parse_action('do(action=__import__("os").system("echo unsafe"))')


def test_parse_rejects_wrong_function_name() -> None:
    with pytest.raises(ValueError):
        parse_action('download(action="Tap", element=[1, 2])')


def test_parse_rejects_positional_arguments() -> None:
    with pytest.raises(ValueError):
        parse_action('do("Tap")')


def test_normalize_app_name_exact_match() -> None:
    from phone_agent.config.apps import normalize_app_name

    assert normalize_app_name("Settings") == "Settings"
    assert normalize_app_name("Chrome") == "Chrome"


def test_normalize_app_name_case_insensitive() -> None:
    from phone_agent.config.apps import normalize_app_name

    assert normalize_app_name("settings") == "Settings"
    assert normalize_app_name("chrome") == "Chrome"


def test_normalize_app_name_alias() -> None:
    from phone_agent.config.apps import normalize_app_name

    assert normalize_app_name("设置") == "Settings"
    assert normalize_app_name("系统设置") == "Settings"
    assert normalize_app_name("微信") == "WeChat"
    assert normalize_app_name("WeChat(微信)") == "WeChat"
    assert normalize_app_name("Twitter(X)") == "Twitter"


def test_normalize_app_name_unknown_returns_none() -> None:
    from phone_agent.config.apps import normalize_app_name

    assert normalize_app_name("NonExistentApp") is None


def test_get_app_registry_summary_contains_header() -> None:
    from phone_agent.config.apps import get_app_registry_summary

    cn = get_app_registry_summary(lang="cn")
    assert "可用应用" in cn
    assert "Launch" in cn

    en = get_app_registry_summary(lang="en")
    assert "Available Apps" in en
    assert "Launch" in en


def test_get_app_registry_summary_contains_common_apps() -> None:
    from phone_agent.config.apps import APP_PACKAGES, get_app_registry_summary, normalize_app_name

    summary = get_app_registry_summary(lang="cn")
    assert "Settings" in summary
    assert "Chrome" in summary
    assert "淘宝" in summary
    assert "Google Docs" in summary
    listed = summary.split("\n", 1)[1].replace(", ...", "").split(", ")
    assert all(normalize_app_name(name) is not None for name in listed if name)
    assert set(listed) == set(APP_PACKAGES)


def test_get_app_registry_summary_truncation() -> None:
    from phone_agent.config.apps import get_app_registry_summary

    short = get_app_registry_summary(lang="cn", max_chars=200)
    assert len(short) <= 200
