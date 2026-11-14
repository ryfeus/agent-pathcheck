"""Bounded provider-neutral model/MCP repair loop."""

import json
from hashlib import sha256

from mcp.shared.exceptions import MCPError
from .providers import AnthropicMessages, OpenAIResponses, ProviderError

from agent_pathcheck.presentation import validated_fields
from agent_pathcheck.runtime import MAX_SOURCE_CHARS


MAX_TURNS = 8
MAX_TOOL_CALLS = 6
MAX_LOOKUPS = 8


def api_error(exc):
    """API error messages can echo credentials; report only safe metadata."""
    return {"error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}


def model_tools(discovered, allowed_tools):
    by_name = {tool.name: tool for tool in discovered.tools}
    if set(by_name) != set(allowed_tools):
        raise ValueError(f"MCP server must expose exactly {list(allowed_tools)}")
    result = []
    for name, argument in allowed_tools.items():
        tool = by_name[name]
        schema = tool.input_schema
        if (set(schema.get("properties", {})) != {argument}
                or schema.get("required") != [argument]
                or schema["properties"][argument].get("type") != "string"):
            raise ValueError("Unexpected MCP input schema")
        result.append({
            "type": "function", "name": name, "description": tool.description,
            "parameters": {**schema, "additionalProperties": False}, "strict": True,
        })
    return result


async def run_loop(api, mcp, *, model, instructions, tool_name="run_workflow",
                   max_turns=MAX_TURNS, max_tool_calls=MAX_TOOL_CALLS, provider="openai", max_lookups=MAX_LOOKUPS):
    adapter = {"openai": OpenAIResponses, "anthropic": AnthropicMessages}[provider]()
    allowed_tools = {tool_name: "code"}
    discovered = await mcp.list_tools()
    if any(tool.name == "read_semantic" for tool in discovered.tools):
        allowed_tools["read_semantic"] = "name"
    tools = model_tools(discovered, allowed_tools)
    history = [{"role": "user", "content": "Solve the configured task using its Python policy contract."}]
    result = {
        "model": model, "status": "turn_limit", "final_text": None, "last_report": None,
        "final_text_status": "unverified", **validated_fields(None),
        "tool_calls": [], "lookups": [], "model_usage": [], "tool_activity": [],
        "discovered_tools": [tool["name"] for tool in tools],
        "instructions_sha256": sha256(instructions.encode()).hexdigest(),
    }
    for turn in range(1, max_turns + 1):
        try:
            response = await adapter.request(api, model=model, instructions=instructions,
                                             history=history, tools=tools)
        except ProviderError as exc:
            result.update(status="api_error", **exc.metadata)
            return result
        result["model_usage"].append(response.usage)
        if not response.complete:
            if any(call.name != "read_semantic" for call in response.calls):
                result["last_report"] = None
                result.update(validated_fields(None))
            result["status"] = "model_incomplete"
            return result
        calls = response.calls
        if not calls:
            result["final_text"] = response.text
            result["status"] = "completed" if result["last_report"] else "incomplete"
            return result
        outputs = []
        for call in calls:
            activity = {"seq": len(result["tool_activity"]) + 1, "turn": turn, "name": call.name}
            result["tool_activity"].append(activity)
            if call.name == "read_semantic" and call.name in allowed_tools:
                if len(result["lookups"]) >= max_lookups:
                    result["status"] = "lookup_limit"
                    return result
                entry = {"name": None, "ok": False}
                activity["entry_index"] = len(result["lookups"])
                result["lookups"].append(entry)
                try:
                    arguments = json.loads(call.arguments) if isinstance(call.arguments, str) else call.arguments
                    if (not isinstance(arguments, dict) or set(arguments) != {"name"}
                            or not isinstance(arguments["name"], str) or len(arguments["name"]) > 256):
                        raise ValueError("Lookup arguments must contain only a bounded string name")
                    entry["name"] = arguments["name"]
                    reply = await mcp.call_tool(call.name, arguments)
                    content = "\n".join(part.text for part in reply.content if hasattr(part, "text"))
                    if reply.is_error:
                        raise ValueError(content or "Semantic lookup failed; use a name from the index")
                    output = {"text": content}
                    entry["ok"] = True
                    activity.update(content_sha256=sha256(content.encode()).hexdigest(),
                                    content_chars=len(content))
                except (ValueError, TypeError, MCPError) as exc:
                    output = {"error": str(exc) if not isinstance(exc, MCPError) else "Semantic lookup failed"}
                outputs.append((call, output))
                continue
            # Preserve history, but a new attempt cannot inherit an earlier pass.
            result["last_report"] = None
            result.update(validated_fields(None))
            if len(result["tool_calls"]) >= max_tool_calls:
                result["status"] = "tool_limit"
                return result
            entry = {"name": call.name}
            activity["entry_index"] = len(result["tool_calls"])
            result["tool_calls"].append(entry)
            try:
                argument = allowed_tools.get(call.name)
                if argument is None:
                    raise ValueError("Tool is not allowed")
                arguments = json.loads(call.arguments) if isinstance(call.arguments, str) else call.arguments
                if (not isinstance(arguments, dict) or set(arguments) != {argument}
                        or not isinstance(arguments[argument], str)):
                    raise ValueError(f"Tool arguments must contain only the string {argument}")
                if len(arguments[argument]) > MAX_SOURCE_CHARS:
                    raise ValueError("Tool input exceeds 10000 characters")
                entry["arguments"] = arguments
                reply = await mcp.call_tool(call.name, arguments)
                if reply.is_error or not isinstance(reply.structured_content, dict):
                    raise ValueError("MCP tool failed; no valid structured report was returned")
                output = reply.structured_content
                result["last_report"] = output
                result.update(validated_fields(output))
            except (ValueError, TypeError) as exc:
                output = {"error": str(exc)}
            except MCPError:
                output = {"error": "MCP request failed"}
            entry["output"] = output
            outputs.append((call, output))
        adapter.results(history, outputs)
    return result
