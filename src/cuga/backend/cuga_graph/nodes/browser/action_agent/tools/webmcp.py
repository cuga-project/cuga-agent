import json
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

LAB_FLAG = "enable-web-mcp@1"


def webmcp_mode() -> str:
    mode = os.environ.get("CUGA_WEBMCP_MODE", "none").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return "naive"
    if mode in {"none", "off", "0", "false", "no", "naive", "advanced"}:
        return "none" if mode in {"off", "0", "false", "no"} else mode
    return "none"


def webmcp_enabled() -> bool:
    return webmcp_mode() in {"naive", "advanced"}


def webmcp_advanced_enabled() -> bool:
    return webmcp_mode() == "advanced"


def seed_local_state_for_webmcp(user_data_dir: str | Path) -> None:
    profile = Path(user_data_dir)
    profile.mkdir(parents=True, exist_ok=True)
    local_state = profile / "Local State"
    state: Dict[str, Any] = {}
    if local_state.exists():
        try:
            state = json.loads(local_state.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"not seeding WebMCP flag: {local_state} is not readable JSON ({exc})")
            return
    experiments = state.setdefault("browser", {}).setdefault("enabled_labs_experiments", [])
    if LAB_FLAG not in experiments:
        experiments.append(LAB_FLAG)
    local_state.write_text(json.dumps(state), encoding="utf-8")


DISCOVER_JS = """
async () => {
  let api = null;
  if (typeof navigator.modelContextTesting?.listTools === 'function') api = navigator.modelContextTesting;
  else if (typeof navigator.modelContext?.getTools === 'function') api = navigator.modelContext;
  else if (typeof document.modelContext?.getTools === 'function') api = document.modelContext;
  if (!api) return [];
  try {
    const tools = await (api.listTools ? api.listTools() : api.getTools());
    return tools || [];
  } catch (e) {
    return [];
  }
}
"""


EXECUTE_JS = """
async ({toolName, params, paramsText}) => {
  let api = null;
  if (typeof navigator.modelContextTesting?.executeTool === 'function') api = navigator.modelContextTesting;
  else if (typeof navigator.modelContext?.executeTool === 'function') api = navigator.modelContext;
  else if (typeof document.modelContext?.executeTool === 'function') api = document.modelContext;
  if (!api) return {error: 'WebMCP executeTool is unavailable on this page.'};

  const candidates = [];
  const addCandidate = (target, args) => {
    if (target !== undefined && target !== null) candidates.push([target, args]);
  };

  try {
    const tools = await (api.getTools ? api.getTools() : (api.listTools ? api.listTools() : []));
    const tool = Array.isArray(tools) ? tools.find((item) => item && item.name === toolName) : null;
    addCandidate(tool, params);
    addCandidate(tool, paramsText);
  } catch (_) {}

  addCandidate(toolName, params);
  addCandidate(toolName, paramsText);

  let lastError = null;
  for (const [target, args] of candidates) {
    try {
      return {result: await api.executeTool(target, args)};
    } catch (e) {
      lastError = e;
    }
  }
  return {error: (lastError && lastError.message) ? lastError.message : String(lastError)};
}
"""


async def discover_tools(page) -> List[Dict[str, Any]]:
    if not webmcp_enabled():
        return []
    last_error = None
    for _ in range(12):
        try:
            tools = await page.evaluate(DISCOVER_JS)
        except Exception as exc:
            last_error = exc
            tools = []
        if tools:
            return tools
        try:
            await page.wait_for_timeout(250)
        except Exception:
            await asyncio.sleep(0.25)
    if last_error:
        logger.warning(f"WebMCP tool discovery failed: {last_error}")
    return []


def _parse_schema(schema: Any) -> Dict[str, Any]:
    if isinstance(schema, str):
        try:
            return json.loads(schema)
        except json.JSONDecodeError:
            return {}
    return schema or {}


def _flatten_tool_result(value: Any) -> str:
    result = value
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        if isinstance(parsed, dict) and isinstance(parsed.get("sections"), list):
            paths = [
                item.get("menu_path")
                for item in parsed["sections"]
                if isinstance(item, dict) and item.get("menu_path")
            ]
            if paths:
                return "Available menu paths:\n" + "\n".join(f"- {path}" for path in paths)
        if isinstance(parsed, dict) and isinstance(parsed.get("content"), list):
            texts = [item.get("text", "") for item in parsed["content"] if isinstance(item, dict)]
            joined = "\n".join(text for text in texts if text)
            if joined:
                result = joined
        elif isinstance(parsed, (dict, list)):
            result = json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass
    return "" if result is None else str(result)


def _normalize_tool_params(params: str | Dict[str, Any]) -> tuple[Any, str]:
    if isinstance(params, str):
        text = params
        try:
            return json.loads(params or "{}"), text
        except json.JSONDecodeError:
            return {}, text
    return params or {}, json.dumps(params or {})


async def execute_tool(page, tool: str, params: str | Dict[str, Any] = "{}") -> str:
    param_value, param_text = _normalize_tool_params(params)
    raw = await page.evaluate(
        EXECUTE_JS,
        {"toolName": tool, "params": param_value, "paramsText": param_text},
    )
    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass
    if isinstance(raw, dict) and raw.get("error"):
        raise RuntimeError(f"webmcp_call({tool!r}) failed: {raw['error']}")
    value = raw.get("result") if isinstance(raw, dict) else raw
    result_text = _flatten_tool_result(value)
    logger.info(f"[webmcp_call result] {tool}: {result_text[:500]}")
    return result_text


def format_tools_for_prompt(tools: List[Dict[str, Any]]) -> str:
    if not tools:
        return ""
    lines = [
        "WebMCP tools available on the current page:",
        "Use webmcp_call(tool, params) when one listed tool directly serves the current step.",
        "Params must be a JSON object matching the input schema.",
        "",
    ]
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "<unknown>")
        desc = tool.get("description", "")
        schema = _parse_schema(tool.get("inputSchema"))
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        lines.append(f"- {name}: {desc}".rstrip())
        if props:
            arg_parts = []
            for prop_name, prop_info in props.items():
                prop_info = prop_info if isinstance(prop_info, dict) else {}
                req = " required" if prop_name in required else ""
                arg_parts.append(f"{prop_name} ({prop_info.get('type', 'string')}{req})")
            lines.append(f"  args: {', '.join(arg_parts)}")
            example = {prop_name: f"<{prop_name}>" for prop_name in props}
            lines.append(f"  example: webmcp_call({name!r}, {json.dumps(example)})")
        else:
            lines.append(f"  example: webmcp_call({name!r}, {{}})")
    return "\n".join(lines)
