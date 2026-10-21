"""The server must import, and the README tool table must match the registered tools."""

import asyncio
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

README = Path(__file__).parent.parent / "README.md"


def registered_tools() -> set[str]:
    import src.server as server

    mcp = FastMCP("test")
    original = server.mcp
    server.mcp = mcp
    try:
        server.register_all_tools()
    finally:
        server.mcp = original
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def readme_tools() -> set[str]:
    text = README.read_text()
    section = text.split("## Tool Reference", 1)[1]
    # Stop at the workflow scripts table and at the next top-level section.
    section = re.split(r"\n### Workflow scripts|\n## ", section, maxsplit=1)[0]
    return set(re.findall(r"`([a-z][a-z0-9_]+)`", section))


def test_server_registers_tools():
    assert len(registered_tools()) > 0


def test_readme_lists_exactly_the_registered_tools():
    registered = registered_tools()
    listed = readme_tools()
    assert listed - registered == set(), f"README lists tools that do not exist: {sorted(listed - registered)}"
    assert registered - listed == set(), f"README does not list these tools: {sorted(registered - listed)}"


def test_readme_tool_count_is_correct():
    match = re.search(r"The server registers (\d+) MCP tools", README.read_text())
    assert match, "README must state the tool count"
    assert int(match.group(1)) == len(registered_tools())
