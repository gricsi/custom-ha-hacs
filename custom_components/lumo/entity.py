"""Base entity for Lumo.

Lumo exposes a single OpenAI-compatible surface: GET /models and POST
/chat/completions. There is no Responses API (POST /responses returns 404), so
everything here is built on client.chat.completions.create rather than the
client.responses.create that upstream OmniConv uses.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncGenerator, Callable
from mimetypes import guess_file_type
from pathlib import Path
from typing import TYPE_CHECKING, Any

import orjson

import openai
import voluptuous as vol
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import llm
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify
from openai._streaming import AsyncStream
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionContentPartParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)
from voluptuous_openapi import convert

from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_PERFORMANCE_TRACING,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DOMAIN,
    LOGGER,
    LUMO_ERROR_CODE_KEY,
    LUMO_ERROR_MESSAGE_KEY,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_TEMPERATURE,
    RECOMMENDED_TOP_P,
)

if TYPE_CHECKING:
    from . import LumoConfigEntry

MAX_TOOL_ITERATIONS = 10

# Warn once the account is within this many requests of its per-window quota.
# Lumo reports the remaining budget on every usage payload as
# usage.remaining_limits = {"lite": N, "max": N, "images": N}.
LOW_QUOTA_THRESHOLD = 3


class PerformanceTracker:
    """Track performance metrics for conversation processing."""

    def __init__(self, enabled: bool = False):
        """Initialize performance tracker."""
        self.enabled = enabled
        self.start_time = time.time()
        self.checkpoints: list[tuple[str, float]] = []
        self.last_checkpoint = self.start_time

    def checkpoint(self, name: str) -> None:
        """Record a checkpoint with elapsed time since last checkpoint."""
        if not self.enabled:
            return

        current_time = time.time()
        elapsed = current_time - self.last_checkpoint
        self.checkpoints.append((name, elapsed))
        self.last_checkpoint = current_time

        LOGGER.info("⏱️  %s: %.3fs", name, elapsed)

    def summary(self) -> None:
        """Log a summary of all checkpoints."""
        if not self.enabled or not self.checkpoints:
            return

        total_time = time.time() - self.start_time
        LOGGER.info("=" * 80)
        LOGGER.info("PERFORMANCE SUMMARY - Total: %.3fs", total_time)
        LOGGER.info("=" * 80)

        for name, elapsed in self.checkpoints:
            percentage = (elapsed / total_time) * 100 if total_time > 0 else 0
            LOGGER.info("  %s: %.3fs (%.1f%%)", name, elapsed, percentage)

        LOGGER.info("=" * 80)


def lumo_error_detail(err: openai.APIStatusError) -> str:
    """Unwrap Proton's error envelope into something readable.

    Proton answers with {"Code": 2000, "Error": "...", "Details": {...}} where
    the OpenAI SDK expects {"error": {"message": ..., "type": ...}}. The SDK
    therefore leaves err.type as None and stringifies the whole body into
    err.message, so we pull the fields out ourselves.
    """
    body = err.body
    if isinstance(body, dict):
        message = body.get(LUMO_ERROR_MESSAGE_KEY)
        code = body.get(LUMO_ERROR_CODE_KEY)
        if message:
            return f"{message} (Proton code {code})" if code is not None else str(message)
    return str(err)


def _log_remaining_limits(usage: Any) -> None:
    """Log Lumo's per-window request budget, loudly when it is nearly spent."""
    limits = getattr(usage, "remaining_limits", None)
    if not isinstance(limits, dict):
        return

    category = getattr(usage, "applied_limit_category", None)
    remaining = limits.get(category) if category else None

    if isinstance(remaining, int) and remaining <= LOW_QUOTA_THRESHOLD:
        LOGGER.warning(
            "Lumo quota nearly exhausted: %s requests left on the '%s' tier (%s)",
            remaining,
            category,
            limits,
        )
    else:
        LOGGER.debug("Lumo remaining limits: %s (applied: %s)", limits, category)


def _format_structured_output(schema: vol.Schema, llm_api: llm.APIInstance | None) -> dict[str, Any]:
    """Convert a voluptuous schema into a JSON schema for response_format.

    Deliberately minimal: unlike the OpenAI Responses API, Lumo does not enforce
    strict decoding, so rewriting optional properties into required nullable ones
    (as upstream OmniConv does) only makes the model emit nulls.
    """
    return convert(
        schema,
        custom_serializer=(llm_api.custom_serializer if llm_api else llm.selector_serializer),
    )


def _serialize_json(obj: Any) -> str:
    """Serialize object to JSON using orjson for better performance."""
    return orjson.dumps(obj).decode("utf-8")


def _format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> ChatCompletionFunctionToolParam:
    """Format a HA tool as a chat-completions function tool."""
    return ChatCompletionFunctionToolParam(
        type="function",
        function={
            "name": tool.name,
            "description": tool.description or "",
            "parameters": convert(tool.parameters, custom_serializer=custom_serializer),
        },
    )


def _convert_content_to_messages(
    chat_content: list[conversation.Content],
) -> list[ChatCompletionMessageParam]:
    """Convert the HA chat log into chat-completions messages."""
    messages: list[ChatCompletionMessageParam] = []

    for content in chat_content:
        if isinstance(content, conversation.ToolResultContent):
            messages.append(
                ChatCompletionToolMessageParam(
                    role="tool",
                    tool_call_id=content.tool_call_id,
                    content=_serialize_json(content.tool_result),
                )
            )
            continue

        if isinstance(content, conversation.AssistantContent):
            assistant: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": content.content,
            }
            if content.tool_calls:
                assistant["tool_calls"] = [
                    ChatCompletionMessageFunctionToolCallParam(
                        id=tool_call.id,
                        type="function",
                        function={
                            "name": tool_call.tool_name,
                            "arguments": _serialize_json(tool_call.tool_args),
                        },
                    )
                    for tool_call in content.tool_calls
                ]
            # A turn that produced only reasoning is not a valid message.
            if assistant["content"] or assistant.get("tool_calls"):
                messages.append(assistant)
            continue

        if not content.content:
            continue

        if content.role == "system":
            messages.append(ChatCompletionSystemMessageParam(role="system", content=content.content))
        else:
            messages.append(ChatCompletionUserMessageParam(role="user", content=content.content))

    return messages


async def _transform_stream(
    chat_log: conversation.ChatLog,
    stream: AsyncStream[ChatCompletionChunk],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict | conversation.ToolResultContentDeltaDict]:
    """Transform a Lumo chat-completions delta stream into HA format."""
    # index -> partial tool call. Lumo currently emits a complete tool call in a
    # single chunk, but the OpenAI wire format allows arguments to arrive as
    # fragments across chunks, so accumulate either way.
    pending_tool_calls: dict[int, dict[str, str]] = {}
    started = False

    def flush_tool_calls() -> list[llm.ToolInput]:
        tool_inputs: list[llm.ToolInput] = []
        for index in sorted(pending_tool_calls):
            call = pending_tool_calls[index]
            raw_arguments = call["arguments"] or "{}"
            try:
                tool_args = json.loads(raw_arguments)
            except json.JSONDecodeError as err:
                raise HomeAssistantError(
                    f"Lumo returned malformed arguments for tool {call['name']!r}: {raw_arguments}"
                ) from err
            tool_inputs.append(
                llm.ToolInput(
                    id=call["id"],
                    tool_name=call["name"],
                    tool_args=tool_args,
                )
            )
        pending_tool_calls.clear()
        return tool_inputs

    async for chunk in stream:
        LOGGER.debug("Received chunk: %s", chunk)

        if chunk.usage is not None:
            chat_log.async_trace(
                {
                    "stats": {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                }
            )
            _log_remaining_limits(chunk.usage)

        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        if delta is not None:
            # Lumo returns its reasoning trace on a non-standard `reasoning`
            # field. The SDK keeps unknown fields (extra="allow").
            reasoning = getattr(delta, "reasoning", None)

            if not started and (delta.role or delta.content or delta.tool_calls or reasoning):
                yield {"role": "assistant"}
                started = True

            if delta.refusal:
                raise HomeAssistantError(f"Lumo refused the request: {delta.refusal}")

            if reasoning:
                yield {"thinking_content": reasoning}

            if delta.content:
                yield {"content": delta.content}

            for tool_call in delta.tool_calls or []:
                slot = pending_tool_calls.setdefault(tool_call.index, {"id": "", "name": "", "arguments": ""})
                if tool_call.id:
                    slot["id"] = tool_call.id
                if tool_call.function:
                    if tool_call.function.name:
                        slot["name"] = tool_call.function.name
                    if tool_call.function.arguments:
                        slot["arguments"] += tool_call.function.arguments

        if choice.finish_reason:
            if choice.finish_reason == "length":
                LOGGER.warning(
                    "Lumo stopped at the max_tokens limit; the answer is truncated. "
                    "Raise 'Maximum tokens to return in response' in the subentry options."
                )
            elif choice.finish_reason == "content_filter":
                raise HomeAssistantError("Lumo stopped the response: content filter triggered")

            if pending_tool_calls:
                if not started:
                    yield {"role": "assistant"}
                    started = True
                yield {"tool_calls": flush_tool_calls()}

    # The stream ended without a terminal finish_reason.
    if pending_tool_calls:
        if not started:
            yield {"role": "assistant"}
        yield {"tool_calls": flush_tool_calls()}


class LumoBaseLLMEntity(Entity):
    """Lumo base LLM entity."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: LumoConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Proton",
            model=subentry.data.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL),
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        self._cached_formatted_tools: list[ChatCompletionFunctionToolParam] | None = None
        self._cached_tools_hash: int | None = None

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure_name: str | None = None,
        structure: vol.Schema | None = None,
    ) -> None:
        """Generate an answer for the chat log."""
        options = self.subentry.data

        perf = PerformanceTracker(enabled=options.get(CONF_PERFORMANCE_TRACING, False))
        perf.checkpoint("Start _async_handle_chat_log")

        messages = _convert_content_to_messages(chat_log.content)
        perf.checkpoint("Convert chat content to chat-completions format")

        model_args: dict[str, Any] = {
            "model": options.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL),
            "messages": messages,
            "max_tokens": options.get(CONF_MAX_TOKENS, RECOMMENDED_MAX_TOKENS),
            "top_p": options.get(CONF_TOP_P, RECOMMENDED_TOP_P),
            "temperature": options.get(CONF_TEMPERATURE, RECOMMENDED_TEMPERATURE),
            "user": chat_log.conversation_id,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if reasoning_effort := options.get(CONF_REASONING_EFFORT, RECOMMENDED_REASONING_EFFORT):
            model_args["reasoning_effort"] = reasoning_effort

        perf.checkpoint("Build model args")

        if chat_log.llm_api:
            current_tools_hash = hash(tuple((tool.name, type(tool).__name__) for tool in chat_log.llm_api.tools))

            if self._cached_formatted_tools is None or self._cached_tools_hash != current_tools_hash:
                self._cached_formatted_tools = [
                    _format_tool(tool, chat_log.llm_api.custom_serializer) for tool in chat_log.llm_api.tools
                ]
                self._cached_tools_hash = current_tools_hash
                LOGGER.debug("Formatted and cached %s tools", len(self._cached_formatted_tools))

            if self._cached_formatted_tools:
                model_args["tools"] = self._cached_formatted_tools

        perf.checkpoint(f"Format tools ({len(model_args.get('tools', []))} tools)")

        last_content = chat_log.content[-1]

        if last_content.role == "user" and last_content.attachments:
            files = await async_prepare_files_for_prompt(
                self.hass,
                [(a.path, a.mime_type) for a in last_content.attachments],
            )
            perf.checkpoint(f"Process attachments ({len(files)} files)")
            last_message = messages[-1]
            assert last_message["role"] == "user" and isinstance(last_message["content"], str)
            last_message["content"] = [
                {"type": "text", "text": last_message["content"]},
                *files,
            ]

        if structure and structure_name:
            model_args["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": slugify(structure_name),
                    "schema": _format_structured_output(structure, chat_log.llm_api),
                    "strict": False,
                },
            }

        client = self.entry.runtime_data

        perf.checkpoint("Prepare API request")

        for _iteration in range(MAX_TOOL_ITERATIONS):
            perf.checkpoint(f"Start iteration {_iteration + 1}/{MAX_TOOL_ITERATIONS}")

            try:
                stream = await client.chat.completions.create(**model_args)

                perf.checkpoint("Lumo API call completed, start streaming")

                messages.extend(
                    _convert_content_to_messages(
                        [
                            content
                            async for content in chat_log.async_add_delta_content_stream(
                                self.entity_id, _transform_stream(chat_log, stream)
                            )
                        ]
                    )
                )

                perf.checkpoint("Streaming & content conversion completed")
            except openai.RateLimitError as err:
                detail = lumo_error_detail(err)
                LOGGER.error("Rate limited by Lumo: %s", detail)
                raise HomeAssistantError(f"Lumo rate limit reached: {detail}") from err
            except openai.AuthenticationError as err:
                detail = lumo_error_detail(err)
                LOGGER.error("Lumo rejected the API key: %s", detail)
                raise HomeAssistantError(f"Lumo authentication failed: {detail}") from err
            except openai.APIStatusError as err:
                detail = lumo_error_detail(err)
                LOGGER.error("Error talking to Lumo (HTTP %s): %s", err.status_code, detail)
                raise HomeAssistantError(f"Error talking to Lumo: {detail}") from err
            except openai.OpenAIError as err:
                LOGGER.error("Error talking to Lumo: %s", err)
                raise HomeAssistantError("Error talking to Lumo") from err

            if not chat_log.unresponded_tool_results:
                perf.checkpoint("No more tool calls, conversation complete")
                break

        perf.summary()


async def async_prepare_files_for_prompt(
    hass: HomeAssistant, files: list[tuple[Path, str | None]]
) -> list[ChatCompletionContentPartParam]:
    """Append files to a prompt.

    Caller needs to ensure that the files are allowed.

    Both Lumo models advertise vision. There is no file/document part on the
    chat-completions endpoint, so PDFs and other attachments are rejected.
    """

    def append_files_to_content() -> list[ChatCompletionContentPartParam]:
        content: list[ChatCompletionContentPartParam] = []

        for file_path, provided_mime_type in files:
            if not file_path.exists():
                raise HomeAssistantError(f"`{file_path}` does not exist")

            mime_type = provided_mime_type or guess_file_type(file_path)[0]

            if not mime_type or not mime_type.startswith("image/"):
                raise HomeAssistantError(f"Lumo only accepts images as attachments; `{file_path}` is not an image")

            base64_file = base64.b64encode(file_path.read_bytes()).decode("utf-8")

            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{base64_file}", "detail": "auto"},
                }
            )

        return content

    return await hass.async_add_executor_job(append_files_to_content)
