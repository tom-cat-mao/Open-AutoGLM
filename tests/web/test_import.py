"""Import smoke for the optional web package."""


def test_phone_agent_web_imports_cleanly():
    import phone_agent.web as web
    import phone_agent.web.app as app

    assert web.WebRunBridge is not None
    assert web.WebEventMiddleware is not None
    assert app.create_ui is not None


def test_web_cli_help_parser_defaults():
    from phone_agent.web.__main__ import build_parser

    args = build_parser().parse_args([])
    assert args.port == 8080
    assert args.device_id is None
    assert args.model is None
