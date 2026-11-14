"""Provider wire formats; execution authorization stays in the common loop."""

from dataclasses import dataclass
import json
from typing import Any


class ProviderError(Exception):
    def __init__(self, exc):
        self.metadata = {"error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}


@dataclass
class ToolCall:
    name: str
    arguments: Any
    call_id: str


@dataclass
class Turn:
    complete: bool
    calls: list[ToolCall]
    text: str
    usage: dict


class OpenAIResponses:
    async def request(self, api, *, model, instructions, history, tools):
        try:
            response = await api.responses.create(
                model=model, instructions=instructions, input=history,
                tools=tools, parallel_tool_calls=False,
                reasoning={"effort": "low"}, max_output_tokens=2500,
                store=False, include=["reasoning.encrypted_content"],
            )
        except Exception as exc:
            raise ProviderError(exc) from None
        history.extend(response.output)
        return Turn(response.status == "completed",
                    [ToolCall(item.name, item.arguments, item.call_id) for item in response.output
                     if item.type == "function_call"], response.output_text,
                    response.usage.model_dump(mode="json") if response.usage else {})

    def results(self, history, outputs):
        history.extend({"type": "function_call_output", "call_id": call.call_id,
                        "output": json.dumps(output, allow_nan=False)} for call, output in outputs)


class AnthropicMessages:
    async def request(self, api, *, model, instructions, history, tools):
        definitions = [{"name": tool["name"], "description": tool["description"],
                        "input_schema": tool["parameters"]} for tool in tools]
        try:
            response = await api.messages.create(
                model=model, system=instructions, messages=history, tools=definitions,
                tool_choice={"type": "auto", "disable_parallel_tool_use": True}, max_tokens=2500,
            )
        except Exception as exc:
            raise ProviderError(exc) from None
        content = [block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump")
                   else vars(block) for block in response.content]
        history.append({"role": "assistant", "content": content})
        return Turn(response.stop_reason in ("end_turn", "tool_use"),
                    [ToolCall(block["name"], block["input"], block["id"]) for block in content
                     if block["type"] == "tool_use"],
                    "".join(block["text"] for block in content if block["type"] == "text"),
                    response.usage.model_dump(mode="json") if response.usage else {})

    def results(self, history, outputs):
        # All results for an assistant turn belong in one following user message.
        history.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.call_id,
             "content": json.dumps(output, allow_nan=False), "is_error": "error" in output}
            for call, output in outputs]})
