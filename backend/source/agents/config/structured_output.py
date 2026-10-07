"""Provider-portable structured handoffs for domain subagents."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

INVALID_HANDOFF_MESSAGE = "invalid_handoff: structured handoff repair required."
UNTRUSTED_ROUTING_ACTIONS = frozenset(
    {"delegate_to_quant_data", "delegate_to_quant_researcher"}
)


def invalid_handoff_message(_error: Exception) -> str:
    """Return a bounded diagnostic without exposing model output or provider details."""
    return INVALID_HANDOFF_MESSAGE


def _messages_from_state(state: Any) -> list[Any]:
    if isinstance(state, dict):
        messages = state.get("messages", [])
    else:
        messages = getattr(state, "messages", [])
    return list(messages) if isinstance(messages, (list, tuple)) else []


def _has_invalid_handoff(state: Any) -> bool:
    return any(
        isinstance(message, (AIMessage, ToolMessage))
        and str(message.content).startswith("invalid_handoff:")
        for message in _messages_from_state(state)
    )


class JsonHandoffMiddleware(AgentMiddleware):
    """Finalize a subagent as JSON text and validate it with Pydantic.

    DeepSeek can call ordinary tools reliably but currently emits malformed calls
    for LangChain's artificial ``ToolStrategy`` output tool. This middleware keeps
    domain tools unchanged and validates the final text response instead. One
    tool-free JSON-mode repair is allowed; domain tools are never repeated.
    """

    def __init__(self, schema: type[BaseModel]) -> None:
        self.schema = schema
        compact_schema = json.dumps(
            schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._instructions = (
            "When you have finished all required tool calls, your final response MUST be "
            "exactly one raw JSON object and nothing else. Do not use a tool call, Markdown "
            "fence, or prose around it. The object must validate against this JSON schema: "
            f"{compact_schema}"
        )

    def _request_with_instructions(self, request):
        existing = request.system_message.text if request.system_message else ""
        prompt = f"{existing}\n\n{self._instructions}" if existing else self._instructions
        return request.override(
            response_format=None,
            system_message=SystemMessage(content=prompt),
        )

    def _structured(self, response: ModelResponse) -> BaseModel | None:
        if response.structured_response is not None:
            value = response.structured_response
            return value if isinstance(value, self.schema) else self.schema.model_validate(value)
        for message in reversed(response.result):
            if not isinstance(message, AIMessage):
                continue
            if message.tool_calls:
                return None
            content = message.content
            if isinstance(content, str) and content.strip():
                return self.schema.model_validate_json(content)
        raise ValueError("Subagent returned no final JSON object.")

    @staticmethod
    def _with_structured(response: ModelResponse, value: BaseModel) -> ModelResponse:
        next_action = getattr(value, "next_action", None)
        if next_action in UNTRUSTED_ROUTING_ACTIONS:
            value = value.model_copy(update={"next_action": "final_answer"})
        return ModelResponse(result=response.result, structured_response=value)

    def _repair_request(self, request, response: ModelResponse):
        settings = {
            **request.model_settings,
            "response_format": {"type": "json_object"},
        }
        messages = [
            *request.messages,
            *response.result,
            HumanMessage(
                content=(
                    "Your previous final response did not validate. Return one corrected raw "
                    "JSON object now. Do not call tools and do not add commentary."
                )
            ),
        ]
        return self._request_with_instructions(request).override(
            messages=messages,
            tools=[],
            tool_choice=None,
            response_format=None,
            model_settings=settings,
        )

    def wrap_model_call(self, request, handler):
        response = handler(self._request_with_instructions(request))
        try:
            structured = self._structured(response)
        except (ValidationError, ValueError, TypeError):
            try:
                repaired = handler(self._repair_request(request, response))
                structured = self._structured(repaired)
            except Exception:
                return AIMessage(content=INVALID_HANDOFF_MESSAGE)
            if structured is None:
                return AIMessage(content=INVALID_HANDOFF_MESSAGE)
            return self._with_structured(repaired, structured)
        if structured is None:
            return response
        return self._with_structured(response, structured)

    async def awrap_model_call(self, request, handler):
        response = await handler(self._request_with_instructions(request))
        try:
            structured = self._structured(response)
        except (ValidationError, ValueError, TypeError):
            try:
                repaired = await handler(self._repair_request(request, response))
                structured = self._structured(repaired)
            except Exception:
                return AIMessage(content=INVALID_HANDOFF_MESSAGE)
            if structured is None:
                return AIMessage(content=INVALID_HANDOFF_MESSAGE)
            return self._with_structured(repaired, structured)
        if structured is None:
            return response
        return self._with_structured(response, structured)

    def _blocked_message(self, request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=INVALID_HANDOFF_MESSAGE,
            tool_call_id=request.tool_call["id"],
            name=request.tool_call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request, handler) -> ToolMessage | Command[Any]:
        if _has_invalid_handoff(request.state):
            return self._blocked_message(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command[Any]:
        if _has_invalid_handoff(request.state):
            return self._blocked_message(request)
        return await handler(request)


def handoff_middleware(schema: type[BaseModel]) -> JsonHandoffMiddleware:
    """Create the provider-portable finalizer for one handoff contract."""
    return JsonHandoffMiddleware(schema)
