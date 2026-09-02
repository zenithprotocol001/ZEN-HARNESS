"""C3 PromptAssembler: layered prompt boundary framing with tool schema injection.

Three independent defenses, applied in order:

1.  `escape_boundary_tokens()`: any literal boundary token found in the
    user input is replaced with a structurally equivalent escaped form
    (e.g. `</user_message>` -> `&lt;/user_message&gt;`). This is the
    first line of defense against boundary breakouts.

2.  Control tokens: the wrapped user section uses model-specific markers
    `<|user_start|>` and `<|user_end|>`. The mock LLM in fixtures is
    taught to treat anything between these tokens as opaque user data.

3.  Tool schema injection: the system section contains a strictly-typed
    JSON manifest of currently available tools. Tool schemas are filtered
    through the C9 CapabilityPolicy so an agent only "sees" tools it has
    been granted. Untrusted tool names never enter the prompt.

No `Any` types in the public surface. Every `Message` and every
`ToolSchema` is a pydantic model with `extra="forbid"`.
"""

from __future__ import annotations

import html
import re
from typing import Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: str = Field(pattern=r"^(system|user|assistant|tool)$")
    content: str = Field(min_length=0, max_length=32768)


class ToolSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    input_schema: dict = Field(min_length=1)


BOUNDARY_TOKENS: tuple[str, ...] = (
    "<|user_start|>",
    "<|user_end|>",
    "<|system_start|>",
    "<|system_end|>",
    "</user_message>",
    "<user_message>",
    "### SYSTEM",
    "### SYSTEM OVERRIDE",
    "IGNORE PREVIOUS INSTRUCTIONS",
)


_ESCAPE_MAP: dict[str, str] = {
    tok: f"<<ESCAPED:{html.escape(tok)}>>" for tok in BOUNDARY_TOKENS
}

# Build a single regex that matches the longest token first. This
# guarantees that, e.g., "### SYSTEM OVERRIDE" is replaced as a whole
# before "### SYSTEM" can match a prefix of it.
_SORTED_TOKENS = sorted(BOUNDARY_TOKENS, key=len, reverse=True)
_ESCAPE_PATTERN = re.compile("|".join(re.escape(t) for t in _SORTED_TOKENS))


def escape_boundary_tokens(text: str) -> str:
    return _ESCAPE_PATTERN.sub(lambda m: _ESCAPE_MAP[m.group(0)], text)


def build_tool_section(tools: Iterable[ToolSchema]) -> str:
    schemas = list(tools)
    if not schemas:
        return "(no tools available)"
    lines = ["AVAILABLE TOOLS (use exactly these names and arguments):"]
    for i, t in enumerate(schemas, 1):
        lines.append(f"[tool {i}] name={t.name}")
        lines.append(f"  description: {t.description}")
        lines.append(f"  input_schema: {t.input_schema!r}")
    return "\n".join(lines)


def build_prompt(
    system: str,
    messages: list[Message],
    tools: list[ToolSchema],
) -> str:
    """Assemble a hardened prompt.

    Layout:
        <|system_start|>
        {escaped system}
        {tool section}
        <|system_end|>

        <|user_start|>
        {escaped user N}
        <|user_end|>
        ...
    """
    sys_section = escape_boundary_tokens(system)
    tool_section = build_tool_section(tools)
    out: list[str] = []
    out.append("<|system_start|>")
    out.append(sys_section)
    out.append(tool_section)
    out.append("<|system_end|>")
    out.append("")
    for m in messages:
        if m.role == "system":
            continue
        body = escape_boundary_tokens(m.content)
        if m.role == "user":
            out.append("<|user_start|>")
            out.append(body)
            out.append("<|user_end|>")
        else:
            out.append(f"[{m.role}] {body}")
    return "\n".join(out)


class PromptAssembler:
    def __init__(self, tool_schemas_provider: Callable[[str], list[ToolSchema]] | None = None) -> None:
        self._provider = tool_schemas_provider or (lambda agent_id: [])

    def set_tool_provider(self, provider: Callable[[str], list[ToolSchema]]) -> None:
        self._provider = provider

    def assemble(self, agent_id: str, system: str, messages: list[Message]) -> str:
        tools = self._provider(agent_id)
        return build_prompt(system=system, messages=messages, tools=tools)


@plugin("c3_prompt")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    assembler = PromptAssembler()

    def default_provider(agent_id: str) -> list[ToolSchema]:
        policy = ctx.inject("policy")
        registry = ctx.inject("registry")
        tools = ctx.inject("tools")

        if policy is not None and hasattr(policy, "capabilities_of"):
            granted = policy.capabilities_of(agent_id)
        else:
            granted = set()

        if tools is None or not hasattr(tools, "_executors"):
            return []

        if registry is not None and hasattr(registry, "get"):
            if not registry.is_registered(agent_id):
                return []

        out: list[ToolSchema] = []
        for tool_name in getattr(tools, "_executors", {}).keys():
            if tool_name not in granted:
                continue
            schema = tools._schemas.get(tool_name)
            if schema is None:
                continue
            out.append(
                ToolSchema(
                    name=tool_name,
                    description=f"Tool {tool_name}",
                    input_schema=schema.model_json_schema(),
                )
            )
        return out

    assembler.set_tool_provider(default_provider)
    ctx.provide("prompt", assembler)

    async def dispose() -> None:
        ctx.services.pop("prompt", None)

    return dispose
