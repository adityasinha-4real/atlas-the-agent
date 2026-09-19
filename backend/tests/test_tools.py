"""Tool implementations and the registry's safety contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from atlas.tools.base import Tool, ToolResult
from atlas.tools.calculator import CalculatorTool
from atlas.tools.files import build_file_tools
from atlas.tools.registry import ToolRegistry

# Tools raise ToolError on expected failures; the *registry* is the contract that
# turns those into observations. So error paths are exercised through a registry.


# -- calculator -------------------------------------------------------------- #


async def test_calculator_evaluates_expression() -> None:
    result = await CalculatorTool().run(
        CalculatorTool.Args(expression="0.15 * 68000000")
    )
    assert result.ok
    assert result.output == "10200000"


async def test_calculator_supports_functions() -> None:
    result = await CalculatorTool().run(CalculatorTool.Args(expression="sqrt(144) + 1"))
    assert result.ok and result.output == "13"


@pytest.mark.parametrize(
    "expr",
    ["__import__('os')", "open('x')", "1 + foo", "1 / 0", "2 ** ** 3"],
)
async def test_calculator_rejects_unsafe_or_invalid(expr: str) -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    result = await registry.execute("calculator", {"expression": expr})
    assert not result.ok
    assert result.error


# -- file tools + path jail -------------------------------------------------- #


async def test_file_write_then_read_roundtrip(tmp_path: Path) -> None:
    registry = _file_registry(tmp_path)
    write = await registry.execute(
        "file_write", {"path": "notes/out.txt", "content": "hi"}
    )
    assert write.ok
    read = await registry.execute("file_read", {"path": "notes/out.txt"})
    assert read.ok and read.output == "hi"


async def test_file_read_missing_is_error(tmp_path: Path) -> None:
    result = await _file_registry(tmp_path).execute("file_read", {"path": "nope.txt"})
    assert not result.ok


@pytest.mark.parametrize("escape", ["../secret.txt", "../../etc/passwd", "sub/../../x"])
async def test_file_tools_enforce_path_jail(tmp_path: Path, escape: str) -> None:
    registry = _file_registry(tmp_path)
    write = await registry.execute("file_write", {"path": escape, "content": "x"})
    assert not write.ok and "jail" in (write.error or "")
    read = await registry.execute("file_read", {"path": escape})
    assert not read.ok


def _file_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_file_tools(str(root / "ws")):
        registry.register(tool)
    return registry


# -- registry contract ------------------------------------------------------- #


async def test_registry_reports_specs() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    specs = registry.specs()
    assert [s.name for s in specs] == ["calculator"]
    assert "expression" in specs[0].args_schema["properties"]


async def test_registry_unknown_tool_is_observation() -> None:
    result = await ToolRegistry().execute("ghost", {})
    assert not result.ok and "Unknown tool" in (result.error or "")


async def test_registry_validates_arguments() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    result = await registry.execute("calculator", {"wrong": 1})
    assert not result.ok and "Invalid arguments" in (result.error or "")


async def test_registry_traps_timeout() -> None:
    class SlowArgs(BaseModel):
        pass

    class SlowTool(Tool):
        name = "slow"
        description = "sleeps"
        Args = SlowArgs

        async def run(self, args: BaseModel) -> ToolResult:
            await asyncio.sleep(1)
            return ToolResult.success("done")

    registry = ToolRegistry(default_timeout=0.01)
    registry.register(SlowTool())
    result = await registry.execute("slow", {})
    assert not result.ok and "timed out" in (result.error or "")


async def test_registry_traps_unexpected_exception() -> None:
    class BoomArgs(BaseModel):
        pass

    class BoomTool(Tool):
        name = "boom"
        description = "raises"
        Args = BoomArgs

        async def run(self, args: BaseModel) -> ToolResult:
            raise RuntimeError("kaboom")

    registry = ToolRegistry()
    registry.register(BoomTool())
    result = await registry.execute("boom", {})
    assert not result.ok and "kaboom" in (result.error or "")
