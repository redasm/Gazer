from tools.base import Tool
from tools.registry_definitions import list_tool_definitions


class _DummyTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs) -> str:
        return "ok"


def test_list_tool_definitions_filters_with_access_checker() -> None:
    tools = {
        "safe": _DummyTool("safe"),
        "blocked": _DummyTool("blocked"),
    }

    defs = list_tool_definitions(
        tools,
        is_allowed=lambda name, **_: name == "safe",
    )

    assert [item["function"]["name"] for item in defs] == ["safe"]
