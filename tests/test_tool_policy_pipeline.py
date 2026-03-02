from agent.tool_policy_pipeline import (
    apply_tool_policy_pipeline_steps,
    merge_tool_policy_constraints,
)
from tools.registry import ToolPolicy


def test_merge_tool_policy_constraints_intersects_allow_and_unions_deny() -> None:
    base = ToolPolicy(
        allow_names={"exec", "read_file"},
        deny_names={"delete_file"},
        allow_providers={"coding", "web"},
        deny_providers={"danger"},
        allow_model_providers={"openai"},
        deny_model_providers={"badproxy"},
    )

    merged = merge_tool_policy_constraints(
        base,
        allow_names={"read_file", "write_file"},
        deny_names={"exec"},
        allow_providers={"coding"},
        deny_providers={"web"},
        allow_model_providers={"openai", "dashscope"},
        deny_model_providers={"openai"},
    )

    assert merged.allow_names == {"read_file"}
    assert merged.deny_names == {"delete_file", "exec"}
    assert merged.allow_providers == {"coding"}
    assert merged.deny_providers == {"danger", "web"}
    assert merged.allow_model_providers == set()
    assert merged.deny_model_providers == {"badproxy", "openai"}


def test_apply_tool_policy_pipeline_steps_reports_diagnostics() -> None:
    base = ToolPolicy()
    resolved, diagnostics = apply_tool_policy_pipeline_steps(
        base=base,
        steps=[
            {
                "label": "global",
                "overlay": {"deny_names": {"node_invoke"}},
            },
            {
                "label": "persona",
                "overlay": {"allow_providers": {"coding"}},
            },
        ],
    )

    assert resolved.deny_names == {"node_invoke"}
    assert resolved.allow_providers == {"coding"}
    assert len(diagnostics) == 2
    assert diagnostics[0]["label"] == "global"
    assert diagnostics[0]["applied"] is True
    assert diagnostics[1]["label"] == "persona"
    assert diagnostics[1]["changed"] is True


def test_apply_tool_policy_pipeline_steps_ignores_invalid_overlay_field_types() -> None:
    base = ToolPolicy()
    resolved, diagnostics = apply_tool_policy_pipeline_steps(
        base=base,
        steps=[
            {
                "label": "invalid_overlay",
                "overlay": {"allow_names": "node_invoke"},
            }
        ],
    )

    assert resolved.allow_names == set()
    assert diagnostics[0]["applied"] is False
    assert diagnostics[0]["overlay_counts"]["allow_names"] == 0
