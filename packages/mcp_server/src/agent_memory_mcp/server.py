"""MCP Server entry point.

Registers tools and runs the server over stdio.

Usage:
    python -m agent_memory_mcp

Or as a WorkBuddy MCP server (configured in .workbuddy/mcp.json):
    {
      "mcpServers": {
        "agent-memory": {
          "command": "python",
          "args": ["-m", "agent_memory_mcp"],
          "env": {}
        }
      }
    }
"""

from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    ListToolsResult,
    CallToolResult,
    ListToolsRequest,
    CallToolRequest,
)

from agent_memory_mcp.tools.export_tool import ExportTool
from agent_memory_mcp.tools.clean_tool import CleanTool


def create_server() -> Server:
    """Create and configure the MCP server with all tools."""
    server = Server("agent-memory-mcp")

    export_tool = ExportTool()
    clean_tool = CleanTool()

    async def handle_list_tools(request: ListToolsRequest) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                export_tool.get_tool_definition(),
                clean_tool.get_tool_definition(),
            ]
        )

    async def handle_call_tool(request: CallToolRequest) -> CallToolResult:
        name = request.params.name if request.params else ""
        arguments = request.params.arguments if request.params else {}
        if arguments is None:
            arguments = {}

        if name == "export":
            result = await export_tool.run(**arguments)
            return CallToolResult(content=[TextContent(type="text", text=result.to_json())])
        elif name == "clean":
            result = await clean_tool.run(**arguments)
            return CallToolResult(content=[TextContent(type="text", text=result.to_json())])
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                is_error=True,
            )

    server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
    server.add_request_handler("tools/call", CallToolRequest, handle_call_tool)

    return server


async def main_async() -> None:
    """Run the MCP server over stdio."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(main_async())
