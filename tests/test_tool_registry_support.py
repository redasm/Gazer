from tools.registry_errors import format_tool_error
from tools.registry_policy import normalize_tool_policy


def test_format_tool_error_includes_trace_and_hint() -> None:
    text = format_tool_error("TOOL_NOT_FOUND", "missing", trace_id="trc_1")

    assert text.startswith("Error [TOOL_NOT_FOUND]: missing (trace_id=trc_1)")
    assert "Hint:" in text


def test_normalize_tool_policy_filters_wildcard_model_selectors() -> None:
    policy = normalize_tool_policy(
        {
            "allow_model_selectors": ["openai/gpt-4o", "*", "/"],
            "deny_model_selectors": ["anthropic/claude", "*"],
        }
    )

    assert policy.allow_model_selectors == {"openai/gpt-4o"}
    assert policy.deny_model_selectors == {"anthropic/claude"}
