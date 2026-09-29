"""
Prompt utilities for CugaLite - handles prompt creation and tool discovery.
"""

import functools
import json
import os
from typing import Any, Dict, List, Optional

from cuga.config import settings
from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import AppDefinition
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import runtime_defaults_for_model
from cuga.backend.tools_env.registry.utils.schema_utils import json_schema_type

_WEAK_SCHEMA_PROBE_DIRECTIVE = (
    "\n    \n    ⚠️ No declared output schema for this tool. Call it ALONE in its own "
    "```python block and print() the raw result — don't write code in the same block "
    "that indexes, slices, or assumes its shape. Write follow-up code using the real "
    "shape once you see it on your next turn."
)

# Sentinel key the MCP manager injects into ``response_schemas`` when a tool
# declares no ``outputSchema`` and it falls back to a generic placeholder. It
# lets ``is_weak_schema_tool`` tell that placeholder apart from a genuine
# string-returning tool, whose ``success`` schema is byte-identical. Kept in
# sync with mcp_manager.py (a plain literal there to avoid a graph→registry
# import dependency).
_SYNTHETIC_PLACEHOLDER_KEY = "_synthetic_placeholder"

_RICH_SCHEMA_KEYS = frozenset(
    {
        "enum",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "const",
        "default",
        "multipleOf",
        "uniqueItems",
    }
)


def input_schema_adds_detail(schema: Any) -> bool:
    """True when raw Input Schema JSON carries detail Parameters would lose."""
    if not isinstance(schema, dict) or not schema:
        return False
    return _schema_node_adds_detail(schema)


def should_emit_output_schema(response_doc: str, output_schema: Any) -> bool:
    """Emit Output Schema JSON only when Response Schema text is absent."""
    if response_doc and str(response_doc).strip():
        return False
    return isinstance(output_schema, dict) and bool(output_schema)


def _non_null_variants(node: dict) -> list:
    variants: list = []
    for key in ("anyOf", "oneOf"):
        for variant in node.get(key) or []:
            if isinstance(variant, dict) and variant.get("type") != "null":
                variants.append(variant)
    t = node.get("type")
    if isinstance(t, list):
        for x in t:
            if x != "null":
                variants.append({"type": x})
    return variants


def _schema_node_adds_detail(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if "$ref" in node:
        return True
    for map_key in ("$defs", "definitions"):
        defs = node.get(map_key)
        if isinstance(defs, dict) and defs:
            return True
    if any(k in node for k in _RICH_SCHEMA_KEYS):
        return True

    ap = node.get("additionalProperties")
    if isinstance(ap, dict) and ap:
        return True
    if "patternProperties" in node or "contains" in node:
        return True
    if node.get("dependentRequired") or node.get("dependentSchemas") or "if" in node or "not" in node:
        return True
    if any(k in node for k in ("contentEncoding", "contentMediaType")):
        return True

    if "anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list):
        variants = _non_null_variants(node)
        if len(variants) > 1:
            return True
        if len(variants) == 1 and _schema_node_adds_detail(variants[0]):
            return True

    items = node.get("items")
    if isinstance(items, dict):
        if items.get("type") == "object" or "properties" in items or "$ref" in items:
            return True
        if _schema_node_adds_detail(items):
            return True
    elif isinstance(items, list):
        if any(_schema_node_adds_detail(i) for i in items if isinstance(i, dict)):
            return True

    prefix = node.get("prefixItems")
    if "prefixItems" in node and isinstance(prefix, list) and prefix:
        return True

    props = node.get("properties")
    if isinstance(props, dict):
        for prop in props.values():
            if not isinstance(prop, dict):
                continue
            if "$ref" in prop:
                return True
            if "properties" in prop and isinstance(prop.get("properties"), dict):
                return True
            if _schema_node_adds_detail(prop):
                return True

    for variant in node.get("allOf") or []:
        if _schema_node_adds_detail(variant):
            return True

    return False


def _coerce_bool_setting(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


def few_shots_enabled_from_settings() -> bool:
    """Whether MCP few-shots are enabled (prompt block + prefix messages); default True."""
    try:
        v = getattr(settings.advanced_features, "cuga_lite_enable_few_shots", True)
    except Exception:
        return True
    return _coerce_bool_setting(v)


def resolve_cuga_lite_few_shots_enabled(
    configurable: Optional[Dict[str, Any]] = None,
    *,
    model_name: Optional[str] = None,
) -> bool:
    """Few-shot toggle: configurable overrides profile (gpt-oss-20b) overrides TOML."""
    cfg = configurable or {}
    if "cuga_lite_enable_few_shots" in cfg:
        return _coerce_bool_setting(cfg["cuga_lite_enable_few_shots"])
    prof = runtime_defaults_for_model(model_name or "")
    if "cuga_lite_enable_few_shots" in prof:
        return _coerce_bool_setting(prof["cuga_lite_enable_few_shots"])
    return few_shots_enabled_from_settings()


def _configurable_of(run_config: Optional[Any]) -> Dict[str, Any]:
    """Pull ``configurable`` out of a RunnableConfig, tolerating any shape.

    ``run_config`` reaches shortlisting from several call sites and is sometimes
    a plain dict, sometimes absent. Shortlister settings are never important
    enough to fail a run over, so anything unexpected reads as "no overrides".
    """
    if not run_config:
        return {}
    try:
        if isinstance(run_config, dict):
            configurable = run_config.get("configurable")
        else:
            configurable = getattr(run_config, "configurable", None)
        return configurable if isinstance(configurable, dict) else {}
    except Exception:
        return {}


class Tool(BaseModel):
    """
    Represents a matching tool with its name, input schema, reasoning, output schema, params_doc, and response_doc.
    """

    name: str = Field(..., description="The name of the tool.")
    input_: dict = Field(
        ...,
        alias="input",
        description="The input parameters/schema for the tool as a dictionary.",
    )
    reasoning: str = Field(
        ...,
        description="The reasoning from the shortlister agent explaining why this tool is relevant.",
    )
    output_schema: dict = Field(
        default_factory=dict,
        description="The output/response schema for the tool as a dictionary.",
    )
    params_doc: str = Field(
        default="",
        description="Documentation string describing the tool's parameters in a formatted way.",
    )
    response_doc: str = Field(
        default="",
        description="Documentation string describing the tool's response/return value schema.",
    )


class FindToolsOutput(BaseModel):
    """
    Output schema for the find_tools function.
    Returns relevant matching tools for a natural language query (no fixed count).
    """

    tools: List[Tool] = Field(
        ...,
        description="Matching tools ordered by relevance to the query. Include all tools needed for the workflow.",
    )


def _render_find_tools_markdown(
    query: str,
    enriched_tools: List[Tool],
    tool_descriptions: Dict[str, Optional[str]],
) -> str:
    """Assemble find_tools discovery markdown with conditional schema blocks."""
    markdown_lines = [
        f"# Found {len(enriched_tools)} Matching Tool(s)\n",
        f"**Query:** {query}\n",
    ]
    for idx, tool in enumerate(enriched_tools, 1):
        markdown_lines.append(f"## {idx}. `{tool.name}`\n")

        tool_description = tool_descriptions.get(tool.name)
        if tool_description:
            markdown_lines.append(f"**Description:** {tool_description}\n")

        markdown_lines.append(f"**Reasoning:** {tool.reasoning}\n")

        if tool.params_doc:
            markdown_lines.append("**Parameters:**\n")
            markdown_lines.append(f"{tool.params_doc}\n")
        else:
            markdown_lines.append("**Parameters:** No parameters required\n")

        if tool.response_doc:
            markdown_lines.append("**Response Schema:**\n")
            markdown_lines.append(f"{tool.response_doc}\n")

        if tool.input_ and tool.input_ != {} and input_schema_adds_detail(tool.input_):
            markdown_lines.append("**Input Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.input_, indent=2)}\n```\n")

        if should_emit_output_schema(tool.response_doc, tool.output_schema):
            markdown_lines.append("**Output Schema:**\n")
            markdown_lines.append(f"```json\n{json.dumps(tool.output_schema, indent=2)}\n```\n")

        markdown_lines.append("---\n")

    return "\n".join(markdown_lines)


# Bounded LLM retries when the shortlister invents tool names (#546).
_SHORTLIST_NAME_MAX_RETRIES = 2


class PromptUtils:
    """Utilities for creating prompts and finding tools."""

    @staticmethod
    def _partition_shortlist_details(
        result: List[Any],
        valid_names: set,
    ) -> tuple[List[Any], List[str]]:
        """Split shortlister details into known tools vs hallucinated names."""
        valid_details: List[Any] = []
        invalid_names: List[str] = []
        seen: set = set()
        for detail in result or []:
            name = getattr(detail, "name", None)
            if not name or name in seen:
                continue
            seen.add(name)
            if name in valid_names:
                valid_details.append(detail)
            else:
                invalid_names.append(name)
        return valid_details, invalid_names

    @staticmethod
    def _shortlist_retry_instructions(
        base_instructions: str,
        invalid_names: List[str],
    ) -> str:
        feedback = (
            "Your previous response included tool names that are not in the available "
            f"tools list: {', '.join(invalid_names)}. "
            "Reply again using ONLY exact names from the available tools. "
            "Do not invent names."
        )
        base = (base_instructions or "").strip()
        return f"{base}\n\n{feedback}" if base else feedback

    @staticmethod
    def _format_filtered_tool_names_note(invalid_names: List[str]) -> str:
        if not invalid_names:
            return ""
        unique = list(dict.fromkeys(invalid_names))
        quoted = ", ".join(f"`{n}`" for n in unique)
        count = len(unique)
        noun = "name" if count == 1 else "names"
        return (
            f"**Note:** Filtered out {count} unrecognized tool {noun} returned by the shortlister: {quoted}."
        )

    @staticmethod
    async def _ainvoke_shortlister_with_name_validation(
        *,
        chain: Any,
        query: str,
        apps_as_dict: Dict[str, Any],
        tools_as_dict: Dict[str, Any],
        base_instructions: str,
        valid_names: set,
        run_config: Optional[Any] = None,
        max_retries: int = _SHORTLIST_NAME_MAX_RETRIES,
    ) -> tuple[List[Any], List[str]]:
        """Invoke the shortlister and retry when returned names are unknown.

        Returns ``(valid_details, filtered_invalid_names)``. After retries are
        exhausted, unknown names are dropped (never forwarded as discoveries).
        """
        from cuga.backend.cuga_graph.utils.langfuse_tracing import nested_langgraph_invoke_config

        instructions = base_instructions or ""
        accumulated: dict[str, Any] = {}
        seen_invalid: List[str] = []
        seen_invalid_set: set[str] = set()
        for attempt in range(max_retries + 1):
            response = await chain.ainvoke(
                {
                    "input": query,
                    "all_apps": apps_as_dict,
                    "all_tools": tools_as_dict,
                    "instructions": instructions,
                },
                config=nested_langgraph_invoke_config(run_config),
            )
            valid, invalid = PromptUtils._partition_shortlist_details(
                getattr(response, "result", None) or [],
                valid_names,
            )
            for detail in valid:
                accumulated.setdefault(getattr(detail, "name", None), detail)
            for name in invalid:
                if name not in seen_invalid_set:
                    seen_invalid_set.add(name)
                    seen_invalid.append(name)
            if not invalid:
                return list(accumulated.values()), []
            logger.warning(
                "Shortlister returned unrecognized tool names (attempt {}/{}): {}",
                attempt + 1,
                max_retries + 1,
                invalid,
            )
            # Retry only when the shortlist is unusable — avoid 3x cost when
            # mostly-valid results already have names we can keep.
            if accumulated or attempt >= max_retries:
                break
            instructions = PromptUtils._shortlist_retry_instructions(
                base_instructions or "",
                seen_invalid,
            )
        return list(accumulated.values()), seen_invalid

    @staticmethod
    def get_tool_params_str(tool: StructuredTool) -> str:
        """Extract params_str (function signature format) for a tool.

        Args:
            tool: The tool to extract params_str from

        Returns:
            String representation of parameters for function signature
        """
        if hasattr(tool, 'args_schema') and tool.args_schema:
            try:
                if hasattr(tool.args_schema, 'model_json_schema'):
                    schema = tool.args_schema.model_json_schema()
                else:
                    schema = tool.args_schema.schema()
                properties = schema.get('properties', {})
                required = schema.get('required', [])

                params = []
                for name, prop in properties.items():
                    param_type = prop.get('type', 'Any')

                    type_mapping = {
                        'string': 'str',
                        'integer': 'int',
                        'number': 'float',
                        'boolean': 'bool',
                        'array': 'list',
                        'object': 'dict',
                    }
                    python_type = type_mapping.get(param_type, param_type)

                    if name in required:
                        params.append(f"{name}: {python_type}")
                    else:
                        default_val = prop.get('default', None)
                        if default_val is not None:
                            if isinstance(default_val, str):
                                params.append(f"{name}: {python_type} = \"{default_val}\"")
                            else:
                                params.append(f"{name}: {python_type} = {default_val}")
                        else:
                            params.append(f"{name}: {python_type} = None")

                return ', '.join(params) if params else ''
            except Exception as e:
                logger.debug(
                    f"Failed to parse schema for tool {tool.name if hasattr(tool, 'name') else str(tool)}: {e}"
                )
                return "**kwargs"
        else:
            return "**kwargs"

    @staticmethod
    def is_weak_schema_tool(tool: StructuredTool) -> bool:
        """True when a tool has no real declared output schema.

        Covers the OpenAPI-derived case (empty ``response_schemas``) and the
        MCP fallback case, where the manager injects a generic placeholder for a
        tool that declared no ``outputSchema`` (see mcp_manager.py). A genuine
        string-returning tool (an OpenAPI text/plain body, or an MCP tool that
        actually declares ``outputSchema: {"type": "string"}``) produces a
        ``success`` schema *identical* to that placeholder, so we no longer
        match on shape — that suppressed real schemas. Instead the manager tags
        the synthetic placeholder with ``_synthetic_placeholder`` and we trust
        that marker, leaving every genuinely-declared schema intact.
        """
        response_schemas = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_response_schemas'):
            response_schemas = tool.func._response_schemas

        if not response_schemas or not isinstance(response_schemas, dict):
            return True

        return bool(response_schemas.get(_SYNTHETIC_PLACEHOLDER_KEY))

    @staticmethod
    def get_tool_docs(tool: StructuredTool) -> tuple[str, str]:
        """Extract params_doc and response_doc for a tool.

        Args:
            tool: The tool to extract docs from

        Returns:
            Tuple of (params_doc, response_doc)
        """
        params_doc = "No parameters required"
        response_doc = ""

        response_schemas = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_response_schemas'):
            response_schemas = tool.func._response_schemas

        param_constraints = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_param_constraints'):
            param_constraints = tool.func._param_constraints

        if PromptUtils.is_weak_schema_tool(tool):
            response_doc = _WEAK_SCHEMA_PROBE_DIRECTIVE
        elif response_schemas and isinstance(response_schemas, dict) and 'success' in response_schemas:
            success_schema = json.dumps(response_schemas['success'], indent=4)
            response_doc = f"\n    \n    Returns (on success) - Response Schema:\n{success_schema}"

        if hasattr(tool, 'args_schema') and tool.args_schema:
            try:
                if hasattr(tool.args_schema, 'model_json_schema'):
                    schema = tool.args_schema.model_json_schema()
                else:
                    schema = tool.args_schema.schema()
                properties = schema.get('properties', {})
                required = schema.get('required', [])

                params_list = []
                for name, prop in properties.items():
                    param_type = json_schema_type(prop)
                    type_mapping = {
                        'string': 'str',
                        'integer': 'int',
                        'number': 'float',
                        'boolean': 'bool',
                        'array': 'list',
                        'object': 'dict',
                    }
                    python_type = type_mapping.get(param_type, param_type)

                    desc = prop.get('description', '')
                    required_mark = " (required)" if name in required else " (optional)"

                    constraints = param_constraints.get(name, []) or prop.get('constraints', [])
                    constraints_str = ""
                    if constraints:
                        constraints_str = f" [Constraints: {', '.join(constraints)}]"

                    params_list.append(f"- `{name}`: {python_type}{required_mark} - {desc}{constraints_str}")

                params_doc = "\n".join(params_list) if params_list else "No parameters required"
            except Exception:
                params_doc = "No parameters required"

        return params_doc, response_doc

    @staticmethod
    def _build_shortlister_payload(
        all_tools: List[StructuredTool],
        all_apps: List[AppDefinition],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Serialize ``all_tools`` and ``all_apps`` for the shortlister LLM prompt.

        Shared by :meth:`find_tools` (runtime tool discovery) and
        :meth:`shortlist_tool_names` (bind-time cap reduction). Per coderabbit on
        cuga-agent#203, keeping a single payload builder prevents the two callers
        from drifting — both must include ``args_schema``, ``_response_schemas``,
        and ``_param_constraints`` for the LLM to rank tools consistently.
        """
        tools_as_dict: Dict[str, Any] = {}
        for tool in all_tools:
            tool_dict = tool.model_dump()
            if hasattr(tool, 'args_schema') and tool.args_schema:
                try:
                    if hasattr(tool.args_schema, 'schema'):
                        tool_dict['args_schema'] = tool.args_schema.schema()
                    elif hasattr(tool.args_schema, 'model_json_schema'):
                        tool_dict['args_schema'] = tool.args_schema.model_json_schema()
                    else:
                        tool_dict['args_schema'] = {}
                except (AttributeError, TypeError, ValueError) as e:
                    # Narrow to expected serialization failures so unexpected bugs propagate
                    # instead of silently stripping schema (coderabbit on #203).
                    logger.debug(f"Failed to serialize args_schema for tool {tool.name}: {e}")
                    tool_dict['args_schema'] = {}
            else:
                tool_dict['args_schema'] = {}

            if hasattr(tool, 'func'):
                if hasattr(tool.func, '_response_schemas'):
                    tool_dict['_response_schemas'] = tool.func._response_schemas
                if hasattr(tool.func, '_param_constraints'):
                    tool_dict['_param_constraints'] = tool.func._param_constraints

            tools_as_dict[tool.name] = tool_dict

        apps_as_dict = {app.name: app.model_dump() for app in all_apps}
        return tools_as_dict, apps_as_dict

    @staticmethod
    async def find_tools(
        query: str,
        all_tools: List[StructuredTool],
        all_apps: List[AppDefinition],
        llm: Optional[Any] = None,
        run_config: Optional[Any] = None,
        task_context: Optional[str] = None,
    ) -> str:
        """
        Search tools from given applications and return the relevant matching tools with reasoning.

        Ranking is delegated to the configured shortlister strategy (``[shortlister]
        strategy``, default ``"llm"`` — the original behavior). See
        ``docs/design/pluggable-shortlister.md``.

        Args:
            query: A natural language query describing what tools are needed.
            all_tools: List of all available tools
            all_apps: List of all available app definitions
            task_context: The initial user message, kept separate from ``query`` so a
                non-LLM strategy can weight the two (the LLM strategy re-joins them into
                the string it has always sent). When omitted, ``query`` is assumed to be
                already composed.

        Returns:
            str: A markdown-formatted string of matching tools, each with:
                 - name: The tool name
                 - reasoning: Explanation of why this tool is relevant
                 - parameters: Formatted parameter documentation
                 - response schema: Response/return value schema
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
            ShortlistRequest,
            ShortlisterRouter,
            render_tools_markdown,
            run_shortlister,
        )
        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import compose_query

        plan = ShortlisterRouter.resolve(
            settings, seam="discovery", configurable=_configurable_of(run_config)
        )
        # Two conditions, both required. Keying only on catalogue size would cap
        # the *default* LLM path above the threshold — injecting a top_k
        # instruction and truncating the render — which #624 requires stay
        # byte-for-byte unchanged at every N.
        #   1. a non-default ranker is configured, and
        #   2. there is actually something to cut.
        # Set ``threshold = 0`` to always engage the configured strategy.
        engage_cosine = not plan.is_llm_only and len(all_tools) > plan.threshold
        if not engage_cosine:
            plan = plan.model_copy(update={"strategy": "llm", "instance": None})

        result = await run_shortlister(
            plan,
            ShortlistRequest(
                query=query,
                tools=all_tools,
                apps=all_apps,
                task_context=task_context,
                top_k=plan.top_k if engage_cosine else None,
                max_results=plan.max_results if engage_cosine else None,
                llm=llm,
                run_config=run_config,
            ),
        )
        candidates = result.candidates
        # Enforce the render cap here rather than inside a strategy: it is a
        # property of what find_tools may print, not of how a ranker scores.
        # Applied only when the cosine stage is engaged, so the default LLM path
        # keeps its historical "no fixed result count" behavior untouched.
        if engage_cosine and plan.max_results:
            candidates = candidates[: plan.max_results]

        display_query = compose_query(query, task_context) if task_context else query
        return render_tools_markdown(candidates, all_tools, display_query, result.notes)

    @staticmethod
    async def shortlist_tool_names(
        query: str,
        all_tools: List[StructuredTool],
        all_apps: List[AppDefinition],
        llm: Optional[Any] = None,
        top_k: int = 4,
        instructions: Optional[str] = None,
        run_config: Optional[Any] = None,
    ) -> List[str]:
        """Rank tools by relevance to ``query`` and return up to ``top_k`` names (best-first).

        Wraps the same shortlister LLM chain as :meth:`find_tools` but exposes the
        ranked ``APIDetails.name`` list directly. Used by bind-time shortlisting in
        ``resolve_model_with_bind_tools`` when the candidate tool count exceeds the
        configured provider cap.
        """
        if top_k <= 0 or not all_tools:
            return []
        # A whitespace-only query would otherwise invoke the LLM and produce arbitrary
        # rankings, defeating the "no query" failure path in the caller (coderabbit on #203).
        if not query or not query.strip():
            return []

        from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
            ShortlistRequest,
            ShortlisterRouter,
            run_shortlister,
        )

        plan = ShortlisterRouter.resolve(settings, seam="bind_cap", configurable=_configurable_of(run_config))
        # Same gate as find_tools: only a configured non-default ranker may
        # narrow the caller's cap. On the default LLM path ``top_k`` stays
        # exactly what the caller computed, as it always has.
        engage_cosine = not plan.is_llm_only and len(all_tools) > plan.threshold
        if not engage_cosine:
            plan = plan.model_copy(update={"strategy": "llm", "instance": None})
        # ``top_k`` is the provider cap; a configured value may lower it, never raise it.
        effective_top_k = min(top_k, plan.top_k) if (engage_cosine and plan.top_k) else top_k

        result = await run_shortlister(
            plan,
            ShortlistRequest(
                query=query,
                tools=all_tools,
                apps=all_apps,
                top_k=effective_top_k,
                llm=llm,
                run_config=run_config,
                instructions=instructions,
            ),
        )

        # Name validation and its bounded retries now live in the LLM strategy
        # (an embedding ranker cannot invent a name), so this only re-applies the
        # historical defence-in-depth filter: dedupe, drop anything unknown, clamp.
        valid_names = {t.name for t in all_tools}
        ranked: List[str] = []
        seen: set = set()
        for candidate in result.candidates:
            name = getattr(candidate, "name", None)
            if not name or name in seen or name not in valid_names:
                continue
            seen.add(name)
            ranked.append(name)
            if len(ranked) >= effective_top_k:
                break
        return ranked

    @staticmethod
    def create_find_tools_bound(all_tools: List[StructuredTool], all_apps: List[AppDefinition]):
        """Create a bound version of find_tools with all_tools and all_apps pre-bound.

        Args:
            all_tools: List of all available tools
            all_apps: List of all available app definitions

        Returns:
            An async callable that only requires query: str as input and returns a markdown string.
        """
        bound_func = functools.partial(
            PromptUtils.find_tools,
            all_tools=all_tools,
            all_apps=all_apps,
        )

        @functools.wraps(PromptUtils.find_tools)
        async def wrapper(query: str) -> str:
            return await bound_func(query)

        return wrapper


def format_apps_for_prompt(apps) -> list:
    """Normalize app definitions to dicts for Jinja (name, type, description), matching mcp_prompt."""
    processed_apps = []
    if not apps:
        return processed_apps
    for app in apps:
        description = getattr(app, 'description', 'No description available')
        max_length = 1000
        if len(description) > max_length:
            description = description[:max_length] + '...'
        processed_apps.append(
            {
                'name': app.name,
                'type': getattr(app, 'type', 'api'),
                'description': description,
            }
        )
    return processed_apps


def normalize_mcp_few_shot_examples(raw: Any) -> List[Dict[str, str]]:
    """Parse configurable few-shot payloads: JSON string or list of role/content dicts."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw_stripped = raw.strip()
        if not raw_stripped:
            return []
        try:
            raw = json.loads(raw_stripped)
        except json.JSONDecodeError:
            logger.debug("mcp_few_shot_examples: invalid JSON string, ignoring")
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role is None or content is None:
            continue
        out.append({"role": str(role).strip(), "content": str(content)})
    return out


def create_mcp_prompt(
    tools,
    base_prompt=None,
    allow_user_clarification=True,
    return_to_user_cases=None,
    instructions=None,
    apps=None,
    task_loaded_from_file=False,
    is_autonomous_subtask=False,
    prompt_template=None,
    enable_find_tools=False,
    enable_todos=False,
    special_instructions=None,
    skills_enabled: bool = False,
    skills_prompt_section: str = "",
    enable_shell_tool: bool = False,
    sandbox_workspace: str = "/workspace",
    sandbox_env_info: str = "",
    has_knowledge=False,
    few_shot_examples: Optional[List[Dict[str, str]]] = None,
    few_shots_enabled: Optional[bool] = None,
    agents_enabled: bool = False,
    agents_prompt_section: str = "",
):
    """Create a prompt for CodeAct agent that works with MCP tools.

    Args:
        tools: List of available tools
        base_prompt: Optional base prompt to prepend
        allow_user_clarification: If True, agent can ask user for clarification. If False, only final answers allowed.
        return_to_user_cases: Optional list of custom cases (in natural language) when agent should return to user.
                             If None, uses default cases.
        instructions: Optional special instructions to include in the system prompt.
        apps: Optional list of connected apps with their descriptions
        task_loaded_from_file: If True, indicates that the task was loaded from a file
        is_autonomous_subtask: If True, indicates this is an autonomous subtask that should complete without user interaction
        prompt_template: Jinja2 template for the prompt
        enable_find_tools: If True, includes find_tools instructions in the prompt
        enable_todos: If True, includes create_update_todos instructions in the prompt
        skills_enabled: If True, render the skills block (load_skill, available skills list)
        skills_prompt_section: Pre-formatted markdown/XML block from the skills registry
        enable_shell_tool: If True, include run_command / npm / sandbox workspace bullets in the prompt (OpenSandbox shell tools; defaults False in settings)
        sandbox_workspace: Path prefix shown to the agent for sandbox files. Use "/workspace" for opensandbox/e2b (real Docker path) and "." for native/local (relative cwd).
        sandbox_env_info: Human-readable OS/environment string shown to the model when shell tools are enabled (e.g. "macOS 14.5" or "Linux (Ubuntu, Docker container)").
        has_knowledge: If True, include knowledge-base search guidance in the prompt
        few_shot_examples: Unused (few-shots are chat-prefix only in ``cuga_lite_graph``).
        few_shots_enabled: Unused (reserved for API compatibility).
    """
    processed_tools = []
    # Graph passes "" when no DB instructions; still allow CLI/demo env (e.g. cuga start demo_crm).
    if not special_instructions:
        special_instructions = os.getenv("CUGA_POLICIES_CONTENT", "")

    for tool in tools:
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        tool_desc = tool.description if hasattr(tool, 'description') else "No description"

        params_str = PromptUtils.get_tool_params_str(tool)
        params_doc, response_doc = PromptUtils.get_tool_docs(tool)

        processed_tools.append(
            {
                'name': tool_name,
                'description': tool_desc,
                'params_str': params_str,
                'params_doc': params_doc,
                'response_doc': response_doc,
            }
        )

    processed_apps = format_apps_for_prompt(apps)

    if not enable_shell_tool:
        if skills_enabled:
            logger.warning(
                "Skills are enabled but enable_shell_tool=False; the skills block will be suppressed. "
                "Set advanced_features.enable_shell_tool=true to activate skills."
            )
        skills_enabled = False
        skills_prompt_section = ""

    prompt = prompt_template.invoke(
        {
            "base_prompt": base_prompt,
            "apps": processed_apps,
            "allow_user_clarification": allow_user_clarification,
            "return_to_user_cases": return_to_user_cases,
            "instructions": instructions,
            "tools": processed_tools,
            "task_loaded_from_file": task_loaded_from_file,
            "is_autonomous_subtask": is_autonomous_subtask,
            "enable_find_tools": enable_find_tools,
            "enable_todos": enable_todos,
            "special_instructions": special_instructions,
            "skills_enabled": skills_enabled,
            "skills_prompt_section": skills_prompt_section,
            "enable_shell_tool": enable_shell_tool,
            "sandbox_workspace": sandbox_workspace,
            "sandbox_env_info": sandbox_env_info,
            "has_knowledge": has_knowledge,
            "agents_enabled": agents_enabled,
            "agents_prompt_section": agents_prompt_section,
        }
    ).to_string()
    return prompt
