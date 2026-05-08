from dataclasses import fields, is_dataclass
from pathlib import Path
from types import UnionType
from typing import get_args, get_origin

from pydantic import BaseModel

from lvnotes.core.exceptions import CacheError


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def to_jsonable(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | frozenset | set):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"cannot serialize {type(value).__name__}")


def from_jsonable(schema: type[object], payload: object) -> object:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(payload)
    return _coerce(schema, payload)


def _coerce(schema: object, payload: object) -> object:
    origin = get_origin(schema)
    args = get_args(schema)
    if origin is list:
        if not isinstance(payload, list):
            raise CacheError(f"expected list for {schema}")
        return [_coerce(args[0], item) for item in payload]
    if origin is dict:
        if not isinstance(payload, dict):
            raise CacheError(f"expected dict for {schema}")
        return {str(key): _coerce(args[1], item) for key, item in payload.items()}
    if origin is UnionType or origin is object:
        for item_schema in args:
            if item_schema is type(None) and payload is None:
                return None
            try:
                return _coerce(item_schema, payload)
            except Exception:
                continue
        raise CacheError(f"cannot coerce value to {schema}")
    if schema is Path:
        if not isinstance(payload, str):
            raise CacheError("expected string path")
        return Path(payload)
    if schema in (str, int, float, bool):
        if not isinstance(payload, schema):
            if schema is float and isinstance(payload, int):
                return float(payload)
            raise CacheError(f"expected {schema.__name__}")
        return payload
    if is_dataclass(schema):
        if not isinstance(payload, dict):
            raise CacheError(f"expected object for {schema}")
        allowed = {field.name for field in fields(schema)}
        extra = set(payload) - allowed
        if extra:
            raise CacheError(f"unexpected fields for {schema}: {sorted(extra)}")
        values = {}
        for field in fields(schema):
            if field.name not in payload:
                raise CacheError(f"missing field {field.name} for {schema}")
            values[field.name] = _coerce(field.type, payload[field.name])
        return schema(**values)
    return payload
