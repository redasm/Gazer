from agent.channel_command_registry import ChannelCommandRegistry, parse_channel_command


def test_parse_channel_command_supports_slash_plus_and_telegram_bot_suffix():
    parsed = parse_channel_command("/model@mybot set slow openai gpt-4o-mini")
    assert parsed is not None
    command, args = parsed
    assert command == "model"
    assert args[:3] == ["set", "slow", "openai"]

    parsed_plus = parse_channel_command("+router off")
    assert parsed_plus == ("router", ["off"])


def test_command_registry_resolves_alias_and_dispatches():
    registry = ChannelCommandRegistry()
    calls = []

    def _handler(args, ctx):
        calls.append((args, ctx))
        return "ok"

    registry.register("help", _handler, aliases=["h"])
    result = registry.execute(command="h", args=["x"], context={"k": "v"})
    assert result == "ok"
    assert calls == [(["x"], {"k": "v"})]


def test_command_registry_mutating_rule_supports_callable():
    registry = ChannelCommandRegistry()
    registry.register(
        "router",
        lambda args, ctx: "noop",
        mutating=lambda args: bool(args) and str(args[0]).strip().lower() == "off",
    )
    assert registry.is_mutating("router", ["off"]) is True
    assert registry.is_mutating("router", ["show"]) is False
