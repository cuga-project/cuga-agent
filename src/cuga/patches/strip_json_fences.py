"""Monkey-patch to strip markdown JSON fences from LLM responses.

Claude via OpenAI-compatible proxies often wraps JSON output in
```json ... ``` fences even when response_format=json_object is set.
This patch strips those fences before Pydantic/JSON parsing.

Usage: import this module early (before any LLM calls).
"""

import re
import json
import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def _strip_fences(text: str) -> str:
    """Strip markdown fences from JSON text."""
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def patch_pydantic_json_parse():
    """Patch Pydantic's model_validate_json to strip markdown fences and fix types."""
    _original = BaseModel.model_validate_json

    @classmethod
    def _patched_validate_json(cls, json_data, *args, **kwargs):
        if isinstance(json_data, (str, bytes)):
            cleaned = _strip_fences(json_data if isinstance(json_data, str) else json_data.decode())
            # Fix common type mismatches: string → list[str]
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    for fname, finfo in cls.model_fields.items():
                        anno = finfo.annotation
                        if fname in parsed and hasattr(anno, '__origin__') and anno.__origin__ is list:
                            if isinstance(parsed[fname], str):
                                # Split string into list of sentences
                                parsed[fname] = [s.strip() for s in parsed[fname].split('.') if s.strip()]
                    cleaned = json.dumps(parsed)
            except (json.JSONDecodeError, AttributeError):
                pass
            return _original.__func__(cls, cleaned, *args, **kwargs)
        return _original.__func__(cls, json_data, *args, **kwargs)

    BaseModel.model_validate_json = _patched_validate_json
    logger.info("Patched Pydantic model_validate_json to strip fences and fix types")


def patch_json_loads():
    """Patch json.loads to strip markdown fences."""
    _original_loads = json.loads

    def _patched_loads(s, *args, **kwargs):
        if isinstance(s, str):
            stripped = _strip_fences(s)
            try:
                return _original_loads(stripped, *args, **kwargs)
            except json.JSONDecodeError:
                return _original_loads(s, *args, **kwargs)
        return _original_loads(s, *args, **kwargs)

    json.loads = _patched_loads
    logger.info("Patched json.loads to strip markdown JSON fences")


def patch_langchain_output_parser():
    """Patch langchain's PydanticOutputParser to handle markdown-formatted output."""
    from langchain_core.output_parsers import PydanticOutputParser

    _original_parse = PydanticOutputParser.parse

    def _patched_parse(self, text: str):
        # First try the original parse
        try:
            return _original_parse(self, text)
        except Exception as e:
            logger.debug(f"Original parse failed ({e.__class__.__name__}), trying fallbacks for: {text[:80]}...")

        # Try stripping markdown fences
        stripped = _strip_fences(text)
        try:
            return _original_parse(self, stripped)
        except Exception:
            pass

        # Try extracting JSON from anywhere in the text
        try:
            # Find first { and last }
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                json_str = text[start:end + 1]
                return _original_parse(self, json_str)
        except Exception:
            pass

        # Last resort: try to parse markdown-style key-value pairs
        # **key**: value → {"key": "value"}
        import re
        md_pattern = re.compile(r'\*\*(\w+)\*\*:\s*(.+?)(?=\n\*\*|\Z)', re.DOTALL)
        matches = md_pattern.findall(text)
        if matches:
            # Get the schema's field types to handle list fields
            schema_fields = {}
            if hasattr(self, 'pydantic_object'):
                for fname, finfo in self.pydantic_object.model_fields.items():
                    anno = finfo.annotation
                    schema_fields[fname] = anno

            parsed = {}
            for key, value in matches:
                value = value.strip()
                # Try to parse as JSON first
                try:
                    parsed[key] = json.loads(value)
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
                # If schema says this should be a list, split by sentences
                field_type = schema_fields.get(key)
                if field_type and hasattr(field_type, '__origin__') and field_type.__origin__ is list:
                    parsed[key] = [s.strip() for s in value.split('.') if s.strip()]
                else:
                    parsed[key] = value

            # Fill in missing required string fields with empty string
            if hasattr(self, 'pydantic_object'):
                for fname, finfo in self.pydantic_object.model_fields.items():
                    if fname not in parsed and finfo.is_required():
                        anno = finfo.annotation
                        if anno == str:
                            parsed[fname] = ""
                        elif hasattr(anno, '__origin__') and anno.__origin__ is list:
                            parsed[fname] = []

            json_str = json.dumps(parsed)
            try:
                return _original_parse(self, json_str)
            except Exception:
                pass

        # Give up — raise original error
        return _original_parse(self, text)

    PydanticOutputParser.parse = _patched_parse
    logger.info("Patched PydanticOutputParser.parse to handle markdown output")


def patch_json_output_parser():
    """Patch langchain's JsonOutputParser to strip fences and fix types."""
    from langchain_core.output_parsers import JsonOutputParser

    _original_parse = JsonOutputParser.parse

    def _fix_list_fields(parsed: dict, parser_self) -> dict:
        """Fix string→list type mismatches based on pydantic schema."""
        pydantic_obj = getattr(parser_self, 'pydantic_object', None)
        if not pydantic_obj or not isinstance(parsed, dict):
            return parsed
        for fname, finfo in pydantic_obj.model_fields.items():
            anno = finfo.annotation
            if fname in parsed and hasattr(anno, '__origin__') and anno.__origin__ is list:
                if isinstance(parsed[fname], str):
                    parsed[fname] = [s.strip() for s in parsed[fname].split('.') if s.strip()]
        return parsed

    def _patched_json_parse(self, text: str):
        # Try original first
        try:
            return _original_parse(self, text)
        except Exception:
            pass

        # Strip fences
        stripped = _strip_fences(text)
        if stripped != text:
            try:
                result = json.loads(stripped)
                return _fix_list_fields(result, self)
            except Exception:
                pass

        # Extract JSON block
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start:end + 1])
                return _fix_list_fields(result, self)
            except Exception:
                pass

        # Parse markdown format
        import re
        md_pattern = re.compile(r'\*\*(\w+)\*\*:\s*(.+?)(?=\n\*\*|\Z)', re.DOTALL)
        matches = md_pattern.findall(text)
        if matches:
            parsed = {}
            for key, value in matches:
                value = value.strip()
                try:
                    parsed[key] = json.loads(value)
                except (json.JSONDecodeError, ValueError):
                    if value.count('.') >= 1 and len(value) > 50:
                        parsed[key] = [s.strip() for s in value.split('.') if s.strip()]
                    else:
                        parsed[key] = value
            return _fix_list_fields(parsed, self)

        return _original_parse(self, text)

    JsonOutputParser.parse = _patched_json_parse
    logger.info("Patched JsonOutputParser.parse to handle fences and fix types")


def patch_openai_chat_model():
    """Patch ChatOpenAI to strip markdown fences from LLM responses before parsing."""
    from langchain_openai import ChatOpenAI

    _original_generate = ChatOpenAI._generate

    def _patched_generate(self, messages, stop=None, run_manager=None, **kwargs):
        result = _original_generate(self, messages, stop=stop, run_manager=run_manager, **kwargs)
        for gen in result.generations:
            if hasattr(gen, 'message') and hasattr(gen.message, 'content'):
                content = gen.message.content
                if isinstance(content, str) and ('**' in content or '```' in content):
                    # Strip markdown fences
                    stripped = _strip_fences(content)
                    if stripped != content:
                        gen.message.content = stripped
        return result

    ChatOpenAI._generate = _patched_generate

    _original_agenerate = ChatOpenAI._agenerate

    async def _patched_agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        result = await _original_agenerate(self, messages, stop=stop, run_manager=run_manager, **kwargs)
        for gen in result.generations:
            if hasattr(gen, 'message') and hasattr(gen.message, 'content'):
                content = gen.message.content
                if isinstance(content, str) and ('**' in content or '```' in content):
                    stripped = _strip_fences(content)
                    if stripped != content:
                        gen.message.content = stripped
        return result

    ChatOpenAI._agenerate = _patched_agenerate
    logger.info("Patched ChatOpenAI._generate/_agenerate to strip markdown fences from responses")


def patch_pydantic_output_parser_parse_obj():
    """Patch PydanticOutputParser._parse_obj to fix string→list type mismatches."""
    from langchain_core.output_parsers import PydanticOutputParser

    _original_parse_obj = PydanticOutputParser._parse_obj

    def _patched_parse_obj(self, obj):
        if isinstance(obj, dict) and hasattr(self, 'pydantic_object'):
            for fname, finfo in self.pydantic_object.model_fields.items():
                anno = finfo.annotation
                if fname in obj and hasattr(anno, '__origin__') and anno.__origin__ is list:
                    if isinstance(obj[fname], str):
                        obj[fname] = [s.strip() for s in obj[fname].split('.') if s.strip()]
                # Fill missing required fields
                if fname not in obj and finfo.is_required():
                    if anno == str:
                        obj[fname] = ""
                    elif hasattr(anno, '__origin__') and anno.__origin__ is list:
                        obj[fname] = []
        return _original_parse_obj(self, obj)

    PydanticOutputParser._parse_obj = _patched_parse_obj
    logger.info("Patched PydanticOutputParser._parse_obj to fix type mismatches")


def patch_parse_json_markdown():
    """Patch langchain's parse_json_markdown to extract JSON from prose+JSON text.

    Claude often returns reasoning text before the JSON object, e.g.:
        'Looking at the page, I see...\n\n{"thoughts": [...], "next_agent": "ActionAgent"}'

    The original parse_json_markdown only handles:
      1. Pure JSON text
      2. ```json ... ``` fenced blocks
    It fails on prose+JSON. This patch adds a fallback that extracts the JSON
    object by finding the first '{' and matching last '}'.
    """
    import langchain_core.utils.json as lc_json

    _original = lc_json.parse_json_markdown

    def _patched_parse_json_markdown(json_string, *, parser=lc_json.parse_partial_json):
        try:
            return _original(json_string, parser=parser)
        except json.JSONDecodeError:
            pass

        # Fallback: extract JSON object from mixed prose+JSON text
        # Find the first '{' or '[' and the matching last '}' or ']'
        brace_start = json_string.find('{')
        bracket_start = json_string.find('[')

        # Pick whichever comes first
        if brace_start >= 0 and (bracket_start < 0 or brace_start < bracket_start):
            start = brace_start
            end = json_string.rfind('}')
        elif bracket_start >= 0:
            start = bracket_start
            end = json_string.rfind(']')
        else:
            # No JSON structure found at all, raise original error
            return _original(json_string, parser=parser)

        if end > start:
            json_substr = json_string[start:end + 1]
            logger.debug(f"Extracted JSON from prose+JSON text (pos {start}-{end})")
            try:
                return parser(json_substr)
            except json.JSONDecodeError:
                pass

        # Give up — raise original error
        return _original(json_string, parser=parser)

    lc_json.parse_json_markdown = _patched_parse_json_markdown

    # Also patch the reference in JsonOutputParser's module so it picks up the new version
    from langchain_core.output_parsers import json as op_json
    op_json.parse_json_markdown = _patched_parse_json_markdown

    logger.info("Patched parse_json_markdown to extract JSON from prose+JSON text")


# Auto-patch on import
patch_pydantic_json_parse()
patch_langchain_output_parser()
patch_json_output_parser()
patch_openai_chat_model()
patch_pydantic_output_parser_parse_obj()
patch_parse_json_markdown()
