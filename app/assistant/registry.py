from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any, Callable

from app.assistant.models import ToolCallRequest, ToolDescriptor, ToolResult


ToolHandler = Callable[[Any, dict[str, Any]], ToolResult]


def normalize_tool_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().casefold()).strip("_")


class ToolRegistry:
    def __init__(self, *descriptors: ToolDescriptor | Iterable[ToolDescriptor]) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        self._aliases: dict[str, str] = {}
        self._handlers: dict[str, ToolHandler] = {}
        for descriptor in self._flatten_descriptors(descriptors):
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor, handler: ToolHandler | None = None) -> ToolDescriptor:
        if not isinstance(descriptor, ToolDescriptor):
            raise TypeError("ToolRegistry.register expects a ToolDescriptor.")

        key = normalize_tool_name(descriptor.name)
        if not key:
            raise ValueError("ToolDescriptor.name is required.")
        if key in self._tools:
            raise ValueError(f"Tool already registered: {descriptor.name}")

        alias_keys = [normalize_tool_name(alias) for alias in descriptor.aliases]
        for alias_key in alias_keys:
            existing = self._aliases.get(alias_key) or (alias_key if alias_key in self._tools else "")
            if existing:
                raise ValueError(f"Tool alias already registered: {alias_key}")

        self._tools[key] = descriptor
        if handler is not None:
            self._handlers[key] = handler
        for alias_key in alias_keys:
            if alias_key:
                self._aliases[alias_key] = key
        return descriptor

    add = register
    add_descriptor = register

    def unregister(self, name: str) -> ToolDescriptor:
        key = self._resolve_key(name)
        if key is None:
            raise KeyError(name)

        descriptor = self._tools.pop(key)
        self._handlers.pop(key, None)
        for alias, target in list(self._aliases.items()):
            if target == key:
                del self._aliases[alias]
        return descriptor

    def get(self, name: str) -> ToolDescriptor | None:
        key = self._resolve_key(name)
        if key is None:
            return None
        return self._tools.get(key)

    def lookup(self, name: str) -> ToolDescriptor:
        descriptor = self.get(name)
        if descriptor is None:
            raise KeyError(name)
        return descriptor

    require = lookup
    descriptor_for = lookup

    def resolve(self, request: ToolCallRequest | str) -> ToolDescriptor | None:
        name = request.tool_name if isinstance(request, ToolCallRequest) else str(request)
        return self.get(name)

    def handler_for(self, name: str) -> ToolHandler:
        key = self._resolve_key(name)
        if key is None or key not in self._handlers:
            raise LookupError(f"No handler registered for tool: {name}")
        return self._handlers[key]

    def execute(
        self,
        state: Any,
        request_or_name: ToolCallRequest | str,
        args: dict[str, Any] | None = None,
    ) -> ToolResult:
        if isinstance(request_or_name, ToolCallRequest):
            tool_name = request_or_name.tool_name
            arguments = dict(request_or_name.arguments)
        else:
            tool_name = str(request_or_name)
            arguments = dict(args or {})
        return self.handler_for(tool_name)(state, arguments)

    call = execute
    run = execute

    def list_tools(self, category: str | None = None) -> list[ToolDescriptor]:
        if category is None:
            return list(self._tools.values())
        normalized_category = str(category).strip().casefold()
        return [tool for tool in self._tools.values() if tool.category.casefold() == normalized_category]

    def list(self, category: str | None = None) -> list[ToolDescriptor]:
        return self.list_tools(category)

    def descriptors(self) -> list[ToolDescriptor]:
        return self.list_tools()

    def names(self) -> list[str]:
        return [descriptor.name for descriptor in self._tools.values()]

    def _resolve_key(self, name: str) -> str | None:
        key = normalize_tool_name(name)
        if key in self._tools:
            return key
        return self._aliases.get(key)

    @staticmethod
    def _flatten_descriptors(values: tuple[ToolDescriptor | Iterable[ToolDescriptor], ...]) -> list[ToolDescriptor]:
        flattened: list[ToolDescriptor] = []
        for value in values:
            if isinstance(value, ToolDescriptor):
                flattened.append(value)
            elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                flattened.extend(item for item in value if isinstance(item, ToolDescriptor))
            elif value is not None:
                raise TypeError(f"Unsupported registry item: {type(value).__name__}")
        return flattened

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def __iter__(self) -> Iterator[ToolDescriptor]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)


__all__ = ["ToolHandler", "ToolRegistry", "normalize_tool_name"]
