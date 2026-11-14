"""A code-only MCP facade over a host-configured execution function."""

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


def create_code_server(name, description, execute, *, tool_name="run_workflow", read_semantic=None):
    server = MCPServer(name, instructions=description)

    @server.tool(
        name=tool_name, description=description,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
        structured_output=True,
    )
    def run_workflow(code: str) -> dict[str, Any]:
        return execute(code)

    if read_semantic is not None:
        @server.tool(name="read_semantic", description="Read a page or anchor from the active semantic index. Documentation is never evidence.",
                     annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
                     structured_output=False)
        def semantic_lookup(name: str) -> str:
            try:
                return read_semantic(name)
            except ValueError as exc:
                raise ToolError(str(exc)) from None

    return server
