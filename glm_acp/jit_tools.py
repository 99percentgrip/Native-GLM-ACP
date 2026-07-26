"""In-memory deferred tool registry for just-in-time model tool loading."""

from __future__ import annotations

import copy
import hashlib
import math
import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SEARCH_TOOLS_NAME = "search_tools"
SEARCH_TOOLS_DEFINITION: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOLS_NAME,
        "description": (
            "Find and load up to five tools. Use BM25 with a plain-language intent "
            "(maximum 500 characters), or regex with a case-insensitive Python-style "
            "pattern (maximum 200 characters)."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "intent": {
                    "type": "string",
                    "description": (
                        "A natural-language capability query or regex pattern, for example: "
                        "'server metrics CPU RAM' or 'read and edit Python files'."
                    ),
                    "minLength": 1,
                    "maxLength": 500,
                },
                "mode": {
                    "type": "string",
                    "enum": ["bm25", "regex"],
                    "default": "bm25",
                    "description": "Search algorithm. BM25 is the default.",
                },
            },
            "required": ["intent"],
        },
    },
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SAFE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_BM25_CANDIDATES = 1_000
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "i",
        "in",
        "of",
        "on",
        "the",
        "to",
        "tool",
        "tools",
        "use",
        "with",
    }
)


@dataclass(frozen=True)
class DeferredTool:
    """One model-facing schema and its optional MCP invocation route."""

    name: str
    schema: dict[str, Any]
    source: str = "native"
    server: str | None = None
    remote_name: str | None = None
    read_only: bool = False
    defer_loading: bool = True
    mcp_schema: dict[str, Any] | None = None


class ToolSearchError(ValueError):
    """Invalid or unsafe local tool-search input."""


class DeferredToolRegistry:
    """BM25/regex registry whose schemas are exposed only when selected."""

    def __init__(self, definitions: Iterable[dict[str, Any]] = ()) -> None:
        self._tools: dict[str, DeferredTool] = {}
        self._search_texts: dict[str, str] = {}
        self._term_frequencies: dict[str, Counter[str]] = {}
        self._document_lengths: dict[str, int] = {}
        self._document_frequency: Counter[str] = Counter()
        self._inverted_index: dict[str, set[str]] = {}
        self._registration_order: dict[str, int] = {}
        self._next_registration_order = 0
        self._total_document_length = 0
        self._regex_corpus = ""
        self._regex_offsets: list[int] = []
        self._regex_names: list[str] = []
        self._regex_dirty = True
        for definition in definitions:
            self.register_native(definition)

    def register_native(self, schema: dict[str, Any]) -> DeferredTool:
        function = schema.get("function", {}) if isinstance(schema, dict) else {}
        name = str(function.get("name", "")).strip()
        if not name or name == SEARCH_TOOLS_NAME:
            raise ValueError("Deferred native tools require a non-gateway function name")
        record = DeferredTool(name=name, schema=copy.deepcopy(schema))
        self._replace(record)
        return record

    def register_mcp_tools(
        self, server: str, tools: Iterable[dict[str, Any]]
    ) -> list[DeferredTool]:
        """Register valid MCP ``tools/list`` descriptors and retain their route."""
        registered: list[DeferredTool] = []
        for descriptor in tools:
            if not isinstance(descriptor, dict):
                continue
            remote_name = str(descriptor.get("name", "")).strip()
            input_schema = descriptor.get("inputSchema")
            if (
                not remote_name
                or not isinstance(input_schema, dict)
                or input_schema.get("type") != "object"
            ):
                continue
            public_name = self._public_mcp_name(server, remote_name)
            description = str(descriptor.get("description", "")).strip()
            if not description:
                description = str(descriptor.get("title", "")).strip()
            schema = {
                "type": "function",
                "function": {
                    "name": public_name,
                    "description": description or f"Call {remote_name} on MCP server {server}.",
                    "parameters": copy.deepcopy(input_schema),
                },
            }
            annotations = descriptor.get("annotations")
            read_only = bool(
                isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
            )
            record = DeferredTool(
                name=public_name,
                schema=schema,
                source="mcp",
                server=server,
                remote_name=remote_name,
                read_only=read_only,
                mcp_schema=copy.deepcopy(descriptor),
            )
            self._replace(record)
            registered.append(record)
        return registered

    def _replace(self, record: DeferredTool) -> None:
        previous = self._term_frequencies.pop(record.name, None)
        if previous is not None:
            for term in previous:
                self._document_frequency[term] -= 1
                if self._document_frequency[term] <= 0:
                    del self._document_frequency[term]
                names = self._inverted_index.get(term)
                if names is not None:
                    names.discard(record.name)
                    if not names:
                        del self._inverted_index[term]
            self._total_document_length -= self._document_lengths.pop(record.name, 0)
            self._search_texts.pop(record.name, None)
        else:
            self._registration_order[record.name] = self._next_registration_order
            self._next_registration_order += 1
        function = record.schema["function"]
        name_tokens = _TOKEN_RE.findall(record.name.lower())
        description = str(function.get("description", ""))
        argument_parts = self._argument_search_parts(function.get("parameters", {}))
        weighted_tokens = [
            *name_tokens,
            *name_tokens,
            *name_tokens,
            *name_tokens,
            *_TOKEN_RE.findall(description.lower()),
            *[
                token
                for part, weight in argument_parts
                for token in _TOKEN_RE.findall(part.lower())
                for _ in range(weight)
            ],
        ]
        frequencies = Counter(weighted_tokens)
        self._tools[record.name] = record
        self._search_texts[record.name] = " ".join(
            [record.name, description, *(part for part, _weight in argument_parts)]
        )
        self._term_frequencies[record.name] = frequencies
        self._document_lengths[record.name] = len(weighted_tokens)
        self._total_document_length += len(weighted_tokens)
        self._document_frequency.update(frequencies.keys())
        for term in frequencies:
            self._inverted_index.setdefault(term, set()).add(record.name)
        self._regex_dirty = True

    @classmethod
    def _argument_search_parts(
        cls, schema: Any, *, depth: int = 0
    ) -> list[tuple[str, int]]:
        if not isinstance(schema, dict) or depth > 8:
            return []
        parts: list[tuple[str, int]] = []
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name, value in properties.items():
                parts.append((str(name), 2))
                if isinstance(value, dict):
                    description = value.get("description")
                    if isinstance(description, str):
                        parts.append((description, 1))
                    parts.extend(cls._argument_search_parts(value, depth=depth + 1))
        items = schema.get("items")
        if isinstance(items, dict):
            parts.extend(cls._argument_search_parts(items, depth=depth + 1))
        return parts

    def _public_mcp_name(self, server: str, remote_name: str) -> str:
        existing = self._tools.get(remote_name)
        if existing is None and _SAFE_TOOL_NAME_RE.fullmatch(remote_name):
            return remote_name
        if (
            existing is not None
            and existing.source == "mcp"
            and existing.server == server
            and existing.remote_name == remote_name
        ):
            return remote_name
        server_part = re.sub(r"[^A-Za-z0-9_-]+", "_", server).strip("_") or "server"
        tool_part = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_name).strip("_") or "tool"
        prefix = f"mcp__{server_part}__"
        candidate = (prefix + tool_part)[:64]
        collision = self._tools.get(candidate)
        if collision is None or (
            collision.source == "mcp"
            and collision.server == server
            and collision.remote_name == remote_name
        ):
            return candidate
        suffix = hashlib.sha256(f"{server}\0{remote_name}".encode()).hexdigest()[:8]
        return candidate[: 64 - len(suffix) - 2] + "__" + suffix

    def get(self, name: str) -> DeferredTool | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools)

    def search(
        self,
        intent: str,
        *,
        mode: str = "bm25",
        allowed_names: set[str] | None = None,
        limit: int = 5,
    ) -> list[DeferredTool]:
        """Return up to five matches over names, descriptions, and argument metadata."""
        normalized_mode = str(mode).strip().lower() or "bm25"
        if normalized_mode == "regex":
            return self._search_regex(intent, allowed_names=allowed_names, limit=limit)
        if normalized_mode != "bm25":
            raise ToolSearchError("Search mode must be 'bm25' or 'regex'.")
        if len(intent) > 500:
            raise ToolSearchError("BM25 tool-search queries are limited to 500 characters.")
        tokens = [
            token
            for token in _TOKEN_RE.findall(intent.lower())
            if token not in _STOP_WORDS
        ]
        if not tokens:
            return []
        expanded_tokens = list(
            dict.fromkeys(
                variant
                for token in tokens
                for variant in self._query_term_variants(token)
            )
        )
        normalized_intent = " ".join(tokens)
        document_count = len(self._tools)
        average_length = self._total_document_length / max(document_count, 1)
        scored: list[tuple[float, int, str, DeferredTool]] = []
        candidate_names: set[str] = set()
        postings: list[set[str]] = []
        for token in expanded_tokens:
            posting = self._inverted_index.get(token)
            if posting:
                postings.append(posting)
                candidate_names.update(posting)
        if allowed_names is not None:
            candidate_names.intersection_update(allowed_names)
        if len(candidate_names) > _MAX_BM25_CANDIDATES and postings:
            rarest = min(postings, key=len)
            candidate_names = set(
                sorted(
                    (
                        name
                        for name in rarest
                        if allowed_names is None or name in allowed_names
                    ),
                    key=self._registration_order.__getitem__,
                )[:_MAX_BM25_CANDIDATES]
            )
        for name in candidate_names:
            record = self._tools[name]
            if allowed_names is not None and record.name not in allowed_names:
                continue
            frequencies = self._term_frequencies[record.name]
            document_length = self._document_lengths[record.name]
            score = 0.0
            for token in expanded_tokens:
                frequency = frequencies.get(token, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[token]
                inverse_document_frequency = math.log(
                    1
                    + (document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * document_length / max(average_length, 1)
                )
                score += inverse_document_frequency * frequency * 2.5 / denominator
            name_tokens = set(_TOKEN_RE.findall(record.name.lower()))
            score += 3.0 * sum(token in name_tokens for token in tokens)
            if normalized_intent in self._search_texts[record.name].lower():
                score += 2.0
            if score:
                scored.append(
                    (
                        score,
                        -self._registration_order[record.name],
                        record.name,
                        record,
                    )
                )
        scored.sort(reverse=True)
        bounded_limit = min(5, max(1, int(limit)))
        return [item[-1] for item in scored[:bounded_limit]]

    @staticmethod
    def _query_term_variants(token: str) -> tuple[str, ...]:
        variants = [token]
        if len(token) > 4 and token.endswith("ies"):
            variants.append(token[:-3] + "y")
        elif len(token) > 3 and token.endswith("s"):
            variants.append(token[:-1])
        return tuple(variants)

    def _search_regex(
        self,
        pattern: str,
        *,
        allowed_names: set[str] | None,
        limit: int,
    ) -> list[DeferredTool]:
        if len(pattern) > 200:
            raise ToolSearchError("Regex tool-search patterns are limited to 200 characters.")
        if not pattern:
            raise ToolSearchError("Regex tool-search patterns cannot be empty.")
        # Python's stdlib regex engine has no portable timeout. Reject the
        # common catastrophic/backtracking constructs while keeping Anthropic's
        # documented alternation, wildcards, groups, and inline flags.
        if re.search(r"\\[1-9]|\(\?(?:[=!<]|P=)|\([^)]*[+*][^)]*\)[+*{]", pattern):
            raise ToolSearchError("Regex pattern uses an unsafe backtracking construct.")
        try:
            expression = re.compile(pattern, re.IGNORECASE)
        except re.error as error:
            raise ToolSearchError(f"Invalid regex pattern: {error.msg}.") from error
        bounded_limit = min(5, max(1, int(limit)))
        self._prepare_regex_corpus()
        matched: list[DeferredTool] = []
        seen: set[str] = set()
        for result in expression.finditer(self._regex_corpus):
            index = bisect_right(self._regex_offsets, result.start()) - 1
            if index < 0 or index >= len(self._regex_names):
                continue
            boundary = (
                self._regex_offsets[index + 1]
                if index + 1 < len(self._regex_offsets)
                else len(self._regex_corpus)
            )
            if result.end() > boundary:
                continue
            name = self._regex_names[index]
            if name in seen or (allowed_names is not None and name not in allowed_names):
                continue
            seen.add(name)
            matched.append(self._tools[name])
            if len(matched) >= bounded_limit:
                break
        return matched

    def _prepare_regex_corpus(self) -> None:
        if not self._regex_dirty:
            return
        chunks: list[str] = []
        offsets: list[int] = []
        names: list[str] = []
        position = 0
        for name in self._tools:
            text = " ".join(self._search_texts[name].split()) + "\n"
            offsets.append(position)
            names.append(name)
            chunks.append(text)
            position += len(text)
        self._regex_corpus = "".join(chunks)
        self._regex_offsets = offsets
        self._regex_names = names
        self._regex_dirty = False

    def schemas_for(
        self, names: Iterable[str], *, allowed_names: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return schemas in load order, omitting stale or disallowed names."""
        schemas: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in names:
            if name in seen or (allowed_names is not None and name not in allowed_names):
                continue
            record = self._tools.get(name)
            if record is not None:
                schemas.append(copy.deepcopy(record.schema))
                seen.add(name)
        return schemas
