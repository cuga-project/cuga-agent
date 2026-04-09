"""MCP server for documentation search and analysis tools."""

import os
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger
from markdownify import markdownify as md
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from cuga.config import settings

load_dotenv()

mcp = FastMCP("Docs MCP Server")

EXCLUDED_TAGS = {"nav", "footer", "header", "aside", "form", "iframe", "noscript", "script", "style"}
LARGE_PAGE_CHARS = int(os.getenv("DOCSEARCH_LARGE_PAGE_CHARS", "100000"))

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html_to_md(html: str) -> str:
    """Convert HTML to cleaned markdown, collapsing excessive blank lines."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(EXCLUDED_TAGS):
        tag.decompose()
    body = soup.select_one("body") or soup
    raw = md(str(body), strip=["img"])
    # Collapse 3+ consecutive blank lines down to a single blank line
    cleaned = re.sub(r"\n{3,}", "\n\n", raw)
    return cleaned.strip()


async def _fetch_single_page(url: str) -> str:
    timeout_ms = int(os.getenv("DOCSEARCH_TIMEOUT", "15")) * 1000
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=_UA)
            page = await context.new_page()
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            html = await page.content()
        finally:
            await browser.close()

    return _html_to_md(html)


async def _summarize_large_content(content: str) -> str | None:
    """Use LLM to summarize content when it exceeds LARGE_PAGE_CHARS. Returns None on failure."""
    try:
        from cuga.backend.llm.models import LLMManager

        model_config = getattr(getattr(settings, "agent", None), "code", None)
        model_config = getattr(model_config, "model", None) if model_config else None
        if not model_config:
            return None
        cfg = dict(model_config) if hasattr(model_config, "get") else {}
        cfg["max_tokens"] = 4000
        llm = LLMManager().get_model(cfg)
        truncate_at = min(len(content), 60_000)
        chunk = content[:truncate_at]
        if truncate_at < len(content):
            chunk += "\n\n[... content truncated for summarization ...]"
        prompt = (
            "Summarize this documentation page concisely. Preserve key technical details, "
            "main sections, configuration options, and actionable steps. Output only the summary, no preamble."
        )
        msg = HumanMessage(content=f"{prompt}\n\n---\n\n{chunk}")
        resp = await llm.ainvoke([msg])
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:
        logger.warning("LLM summarization failed: %s", e)
        return None


@mcp.tool()
async def search_doc(search_url: str) -> str:
    """Fetch a documentation search page and return its content as markdown.

    Construct `search_url` using the pattern from the playbook, e.g.:
        https://www.ibm.com/docs/en/search/watsonx+orchestrate+release+notes

    Args:
        search_url: Full search page URL with the query already embedded.
    """
    search_url = search_url.strip()
    parsed = urlparse(search_url)
    if parsed.scheme not in ("http", "https"):
        return f"Only http/https URLs are allowed. Rejected: {search_url}"
    try:
        content = await _fetch_single_page(search_url)
        logger.info("search_doc: %s | %d chars", search_url, len(content))
        return content
    except Exception as e:
        return f"[Error loading search page: {e}]"


@mcp.tool()
async def fetch_doc_page(url: str) -> str:
    """Fetch a documentation page by URL and return its full content as markdown.

    Use this to:
    - Load a known docs URL the agent already has
    - Follow any link found within a previous fetch_doc_page result

    Args:
        url: Full documentation URL (http/https) to fetch.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Only http/https URLs are allowed. Rejected: {url}"
    try:
        content = await _fetch_single_page(url)
        header = f"# {url.split('/')[-1].split('?')[0] or 'Documentation'}\n**Source:** {url}\n\n"
        full_content = header + content
        char_count = len(full_content)
        if char_count > LARGE_PAGE_CHARS:
            summary = await _summarize_large_content(full_content)
            if summary:
                logger.info("fetch_doc_page (summarized): %s | %d chars", url, char_count)
                return f"# {url.split('/')[-1].split('?')[0] or 'Documentation'}\n**Source:** {url}\n\n> *Page was large ({char_count:,} chars) — LLM summary below.*\n\n{summary}"
        logger.info("fetch_doc_page: %s | %d chars", url, char_count)
        return full_content
    except Exception as e:
        return f"[Error fetching page: {e}]"


# ---------------------------------------------------------------------------
# Grep filter models (Pydantic)
# ---------------------------------------------------------------------------


class GrepMatch(BaseModel):
    """A single line matching the grep pattern."""

    line_num: int = Field(description="1-based line number in the source content")
    line: str = Field(description="The matching line (with surrounding context if context_lines > 0)")
    section: str | None = Field(
        default=None, description="Markdown section title this match belongs to (e.g. 'Configuration')"
    )


class GrepFilterResult(BaseModel):
    """Structured result of filtering documentation content by pattern."""

    pattern: str = Field(description="The grep pattern that was applied")
    total_matches: int = Field(description="Number of lines that matched")
    matches: list[GrepMatch] = Field(description="Individual matches with line numbers and section context")
    formatted_section: str = Field(
        description="Nice markdown section: matches grouped by source section with headers"
    )


def _keywords_to_pattern(keywords: str) -> str:
    """Convert 'release notes | building agents' to a safe OR regex."""
    terms = [re.escape(k.strip()) for k in keywords.split("|") if k.strip()]
    return "|".join(terms)


def _grep_filter_content(
    content: str,
    pattern: str,
    case_sensitive: bool = False,
    max_matches: int = 50,
) -> GrepFilterResult:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pat = re.compile(pattern, flags)
    except re.error:
        return GrepFilterResult(
            pattern=pattern,
            total_matches=0,
            matches=[],
            formatted_section=f"**Invalid regex pattern:** `{pattern}`",
        )
    lines = content.splitlines()
    section_re = re.compile(r"^#{1,6}\s+(.+)$")
    current_section: str | None = None
    matches: list[GrepMatch] = []
    section_matches: dict[str, list[str]] = {}

    for i, line in enumerate(lines):
        if section_re.match(line.strip()):
            current_section = line.strip()
        if pat.search(line):
            section_title = re.sub(r"^#+\s*", "", current_section) if current_section else "(no section)"
            matches.append(GrepMatch(line_num=i + 1, line=line.strip(), section=section_title))
            if section_title not in section_matches:
                section_matches[section_title] = []
            section_matches[section_title].append(f"  - **L{i + 1}:** {line.strip()}")
            if len(matches) >= max_matches:
                break

    parts = [f"## Grep results for `{pattern}` ({len(matches)} matches)\n"]
    for sect, lines_list in section_matches.items():
        clean_title = (
            re.sub(r"^#+\s*", "", sect) if sect and sect != "(no section)" else sect or "(no section)"
        )
        parts.append(f"### {clean_title}\n")
        parts.extend(lines_list)
        parts.append("")

    return GrepFilterResult(
        pattern=pattern,
        total_matches=len(matches),
        matches=matches[:max_matches],
        formatted_section="\n".join(parts).strip(),
    )


def _filter_grep(
    content: str,
    keywords: str,
    pattern: str = "",
    case_sensitive: bool = False,
    max_matches: int = 50,
) -> GrepFilterResult:
    if keywords and pattern:
        return GrepFilterResult(
            pattern="",
            total_matches=0,
            matches=[],
            formatted_section="**Error:** provide `keywords` or `pattern`, not both.",
        )
    if not keywords and not pattern:
        return GrepFilterResult(
            pattern="",
            total_matches=0,
            matches=[],
            formatted_section="**Error:** provide at least one of `keywords` or `pattern`.",
        )
    max_matches = max(1, min(100, max_matches))
    resolved = _keywords_to_pattern(keywords) if keywords else pattern
    return _grep_filter_content(content, resolved, case_sensitive, max_matches)


@mcp.tool()
def filter_grep(
    content: str,
    keywords: str,
    pattern: str = "",
    case_sensitive: bool = False,
    max_matches: int = 50,
) -> GrepFilterResult:
    """Filter documentation content by keywords or regex. Returns structured result.

    Prefer `keywords` for most searches — separate terms with `|` for OR matching.
    Use `pattern` only when you need raw regex.
    Providing both is an error.

    Examples:
        filter_grep(content, keywords="release notes")
        filter_grep(content, keywords="building agents | catalog | getting started")
        filter_grep(content, keywords="api key | authentication", max_matches=20)
        filter_grep(content, keywords="", pattern=r"https?://\\S+")  # regex fallback

    Args:
        content: Documentation markdown (e.g. from fetch_doc_page).
        keywords: Plain keyword string; separate alternatives with ` | ` (e.g. "release | deprecation").
        pattern: Raw regex — only use when keywords aren't expressive enough.
        case_sensitive: If False (default), match case-insensitively.
        max_matches: Stop after this many matches (1-100).

    Returns:
        GrepFilterResult with matches, line numbers, sections, and formatted markdown.
    """
    return _filter_grep(content, keywords, pattern, case_sensitive, max_matches)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = getattr(settings.server_ports, "docs_mcp", 8113)
    mcp.run(transport="sse", host="127.0.0.1", port=port)
