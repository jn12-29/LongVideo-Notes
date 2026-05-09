from dataclasses import is_dataclass, replace
import json
import re
from typing import TypeVar, cast

from pydantic import BaseModel

from lvnotes.core.exceptions import LLMError
from lvnotes.core.serialization import from_jsonable
from lvnotes.llm.base import LLMClient
from lvnotes.llm.text_helper import complete_text
from lvnotes.llm.types import LLMMessage, LLMRequestOptions, LLMTextResult, TextPart

JsonSchemaT = TypeVar("JsonSchemaT")


def complete_json(
    client: LLMClient,
    messages: list[LLMMessage],
    schema: type[JsonSchemaT],
    options: LLMRequestOptions | None = None,
    max_repair_retries: int = 1,
) -> JsonSchemaT:
    value, _ = complete_json_with_raw(client, messages, schema, options, max_repair_retries)
    return value


def complete_json_with_raw(
    client: LLMClient,
    messages: list[LLMMessage],
    schema: type[JsonSchemaT],
    options: LLMRequestOptions | None = None,
    max_repair_retries: int = 1,
) -> tuple[JsonSchemaT, LLMTextResult]:
    _validate_schema(schema)
    attempts = max_repair_retries + 1
    current_messages = messages
    current_error = ""
    for attempt_index in range(attempts):
        request_options = _json_options(client, options)
        result = complete_text(client, current_messages, request_options)
        try:
            return _parse_and_validate(result.text, schema), result
        except (json.JSONDecodeError, ValueError, LLMError) as exc:
            current_error = str(exc)
            if attempt_index + 1 >= attempts:
                break
            current_messages = _repair_messages(current_error, result.text)
    raise LLMError(f"failed to produce valid JSON: {current_error}")


def _validate_schema(schema: type[JsonSchemaT]) -> None:
    if isinstance(schema, type) and (is_dataclass(schema) or issubclass(schema, BaseModel)):
        return
    raise LLMError("complete_json schema must be a dataclass or pydantic BaseModel type")


def _json_options(client: LLMClient, options: LLMRequestOptions | None) -> LLMRequestOptions:
    request_options = options or LLMRequestOptions()
    if "json_mode" not in client.profile.capabilities:
        return replace(request_options, json_mode=False)
    return replace(request_options, json_mode=True)


def _parse_and_validate(text: str, schema: type[JsonSchemaT]) -> JsonSchemaT:
    payload = _parse_json_text(text)
    try:
        return cast(JsonSchemaT, from_jsonable(schema, payload))
    except Exception as exc:
        raise LLMError(f"JSON schema validation failed: {exc}") from exc


def _parse_json_text(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fenced = _extract_fenced_json(text)
        if fenced is None:
            raise
        return json.loads(fenced)


def _extract_fenced_json(text: str) -> str | None:
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match is None:
        return None
    return match.group(1)


def _repair_messages(error_message: str, raw_text: str) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content=[TextPart(text="You repair invalid JSON outputs. Return only valid JSON.")]),
        LLMMessage(
            role="user",
            content=[TextPart(text=f"Validation error:\n{error_message}\n\nPrevious output:\n{raw_text}")],
        ),
    ]
