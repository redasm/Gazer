from pathlib import Path

import pytest

from tools import coding
from tools.coding import EditFileTool


def test_nexum_edit_diff_module_is_loadable() -> None:
    module = coding._load_nexum_edit_diff_module()
    assert module is not None
    module_path = str(Path(module.__file__).resolve()).replace("\\", "/")
    assert "/external/Nexum/" in module_path


@pytest.mark.asyncio
async def test_edit_file_fuzzy_match_uses_nexum_rules(tmp_path: Path) -> None:
    target = tmp_path / "fuzzy.txt"
    target.write_text("value = 1    +    2\n", encoding="utf-8")

    tool = EditFileTool(tmp_path)
    result = await tool.execute(
        path="fuzzy.txt",
        old_text="value = 1 + 2",
        new_text="value = 3",
    )

    assert "replaced 1 occurrence" in result
    assert "value = 3" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_file_supports_claude_style_param_aliases(tmp_path: Path) -> None:
    target = tmp_path / "alias.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    tool = EditFileTool(tmp_path)
    result = await tool.execute(
        file_path="alias.txt",
        old_string="beta",
        new_string="gamma",
    )

    assert "replaced 1 occurrence" in result
    assert "gamma" in target.read_text(encoding="utf-8")
