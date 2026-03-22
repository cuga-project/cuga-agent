"""MCP server for IBM Documentation search and analysis tools."""

import asyncio
import os
import re
import time
from urllib.parse import urlparse

import httpx
import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastmcp import FastMCP
from loguru import logger
from markdownify import markdownify as md
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from cuga.config import settings

load_dotenv()

mcp = FastMCP("IBM Docs MCP Server")

EXCLUDED_TAGS = {"nav", "footer", "header", "aside", "form", "iframe", "noscript", "script", "style"}


class DocSearch:
    """Search IBM documentation and retrieve full page content as markdown."""

    CONTENT_API_URL = "https://www.ibm.com/docs/api/v1/content"

    def __init__(self):
        self.max_results = int(os.getenv("DOCSEARCH_MAX_RESULTS", "3"))
        self.lang = os.getenv("DOCSEARCH_LANG", "en")
        self.api_url = os.getenv("DOCSEARCH_API_URL", "https://www.ibm.com/docs/api/v1/search")
        self.timeout = int(os.getenv("DOCSEARCH_TIMEOUT", "15"))
        self._encoder = tiktoken.get_encoding("cl100k_base")

        # Parse comma-separated product filter (lowercase for comparison)
        raw_products = os.getenv("DOCSEARCH_PRODUCTS", "").strip()
        self.products = (
            [p.strip().lower() for p in raw_products.split(",") if p.strip()] if raw_products else []
        )
        if self.products:
            logger.info("Product filter active: %s", self.products)
        else:
            logger.info("No product filter configured — all products will be returned")

    def count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken cl100k_base encoding."""
        return len(self._encoder.encode(text))

    def _filter_by_product(self, links: list[dict]) -> list[dict]:
        """Filter links to only include configured products (case-insensitive)."""
        if not self.products:
            return links
        filtered = []
        for link in links:
            product_label = link.get("product", "").lower()
            if any(p in product_label for p in self.products):
                filtered.append(link)
            else:
                logger.debug(
                    "Filtered out: '%s' (product: '%s')", link.get("title", ""), link.get("product", "")
                )
        logger.info("Product filter: %d/%d results matched %s", len(filtered), len(links), self.products)
        return filtered

    async def search(self, query: str, *, max_results: int | None = None) -> str:
        """Search IBM docs and return aggregated markdown context."""
        cap = self.max_results if max_results is None else max_results
        t_start = time.perf_counter()

        links = await self._search_links(query, cap)
        links = self._deduplicate(links)
        total_before_filter = len(links)
        links = self._filter_by_product(links)
        links = links[:cap]

        if not links:
            if self.products and total_before_filter > 0:
                return (
                    f"No results found for product(s): {', '.join(self.products)}. "
                    f"The search returned {total_before_filter} result(s) but none matched "
                    f"the configured product filter. Do NOT retry with a different query — "
                    f"the IBM documentation may not have content for this topic under the "
                    f"specified product(s)."
                )
            return "No results found."

        pages = await self._fetch_pages(links)
        context = self._aggregate(links, pages)

        elapsed = time.perf_counter() - t_start
        token_count = self.count_tokens(context)

        logger.info(
            "DocSearch complete: %d pages collected in %.2fs | context size: %d tokens",
            len(pages),
            elapsed,
            token_count,
        )

        header = (
            f"**IBM Docs Search Results** — {len(pages)} page(s) returned for query: \"{query}\" "
            f"({token_count} tokens, {elapsed:.1f}s). "
            f"This response contains the full content of all matched pages. "
            f"Do not call this tool again for the same topic.\n\n"
        )

        return header + context

    async def _search_links(self, query: str, max_results: int) -> list[dict]:
        """Call IBM docs search API and return list of {title, url, snippet}."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            resp = await client.get(
                self.api_url,
                params={
                    "query": query,
                    "lang": self.lang,
                    "limit": max_results * (5 if self.products else 3),
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return [
            {
                "title": topic["title"].replace("<b>", "").replace("</b>", ""),
                "url": topic["fullurl"],
                "href": topic.get("href", ""),
                "snippet": topic["snippet"].replace("<b>", "").replace("</b>", ""),
                "product": topic.get("product", {}).get("label", ""),
            }
            for topic in data.get("topics", [])
        ]

    def _deduplicate(self, links: list[dict]) -> list[dict]:
        """Remove duplicate pages that differ only by product version."""
        seen = set()
        unique = []
        for link in links:
            parsed = urlparse(link["url"])
            path_parts = parsed.path.split("/")
            normalized = "/".join(p for p in path_parts if not any(c.isdigit() and "." in p for c in p))
            if normalized not in seen:
                seen.add(normalized)
                unique.append(link)
        return unique

    async def _fetch_pages(self, links: list[dict]) -> dict[str, str]:
        """Fetch page content via IBM docs content API and convert to markdown."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html",
        }

        async def fetch_one(client: httpx.AsyncClient, link: dict) -> tuple[str, str]:
            url = link["url"]
            href = link.get("href", "")
            try:
                content_url = f"{self.CONTENT_API_URL}/{href}" if href else url
                resp = await client.get(content_url, follow_redirects=True)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                for tag in soup.find_all(EXCLUDED_TAGS):
                    tag.decompose()

                body = soup.select_one("body") or soup
                return url, md(str(body), strip=["img"]).strip()
            except Exception as e:
                return url, f"[Error fetching page: {e}]"

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            tasks = [fetch_one(client, link) for link in links]
            pairs = await asyncio.gather(*tasks)

        return dict(pairs)

    def _aggregate(self, links: list[dict], pages: dict[str, str]) -> str:
        """Combine all page content into a single context string."""
        parts = []
        for link in links:
            url = link["url"]
            content = pages.get(url, "")
            parts.append(
                f"# {link['title']}\n**Source:** {url}\n**Product:** {link['product']}\n\n{content}\n"
            )
        return "\n---\n\n".join(parts)


searcher = DocSearch()


# ---------------------------------------------------------------------------
# FastMCP Tools
# ---------------------------------------------------------------------------

LARGE_PAGE_CHARS = int(os.getenv("DOCSEARCH_LARGE_PAGE_CHARS", "100000"))


class FetchDocPageResult(BaseModel):
    """Structured result of fetching an IBM documentation page."""

    summary: str | None = Field(
        default=None,
        description="LLM-generated summary when page exceeded 100k chars; null otherwise",
    )
    content: str = Field(description="Full page content in markdown")
    url: str = Field(description="Source URL")
    char_count: int = Field(description="Character count of content")
    was_summarized: bool = Field(
        default=False,
        description="True if content was large and an LLM summary was generated",
    )


def _is_allowed_docs_url(url: str) -> bool:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False
    netloc = (parsed.netloc or "").lower()
    host = netloc.split(":")[0]
    path = (parsed.path or "/").lower()
    if not (host == "ibm.com" or host.endswith(".ibm.com")):
        return False
    return path.startswith("/docs") or path.startswith("/support")


async def _fetch_single_page(url: str) -> str:
    timeout_ms = int(os.getenv("DOCSEARCH_TIMEOUT", "15")) * 1000
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            html = await page.content()
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(EXCLUDED_TAGS):
        tag.decompose()
    body = soup.select_one("body") or soup
    return md(str(body), strip=["img"]).strip()


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
            "Summarize this IBM documentation page concisely. Preserve key technical details, "
            "main sections, configuration options, and actionable steps. Output only the summary, no preamble."
        )
        msg = HumanMessage(content=f"{prompt}\n\n---\n\n{chunk}")
        resp = await llm.ainvoke([msg])
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception as e:
        logger.warning("LLM summarization failed: %s", e)
        return None


@mcp.tool()
async def ibm_search_doc(query: str, max_results: int = 3) -> str:
    """Search IBM documentation and return full page content as markdown.

    A single call searches the IBM docs API, fetches up to `max_results` full
    pages, and returns their complete content aggregated into one response.
    This is already a comprehensive result — do NOT call this tool multiple
    times for the same topic or to "get more detail".  One call is enough.

    Use a broad, descriptive query for best results (e.g. "MQ persistent
    messaging configuration" rather than just "MQ").

    Args:
        query: Search query for IBM docs (e.g. "kubernetes deployment", "MQ configuration").
        max_results: Maximum number of doc pages to return (1-5). Defaults to 3.
    """
    cap = min(max(max_results, 1), 5)
    return await searcher.search(query, max_results=cap)


@mcp.tool()
async def fetch_doc_page(url: str) -> FetchDocPageResult | str:
    """Fetch a single IBM documentation page by URL. Returns structured result with summary when page is large (>100k chars).

    Only ibm.com/docs and ibm.com/support URLs are allowed. Use this when:
    - The user pastes an IBM docs URL and asks what it says
    - The user wants to visit "the second result" or a specific link from earlier search output
    - The user asks to open or fetch a linked page from the documentation

    For initial discovery, use ibm_search_doc instead. Do not use fetch_doc_page to re-fetch a page you already have from ibm_search_doc.

    Args:
        url: Full IBM documentation URL (e.g. https://www.ibm.com/docs/en/cloud-pak-for-data/4.8.0?topic=deployment).
    """
    url = url.strip()
    if not _is_allowed_docs_url(url):
        return f"Only IBM documentation URLs (ibm.com/docs, ibm.com/support) are allowed. Rejected: {url}"
    try:
        content = await _fetch_single_page(url)
        header = f"# {url.split('/')[-1].split('?')[0] or 'Documentation'}\n**Source:** {url}\n\n"
        full_content = header + content
        char_count = len(full_content)
        summary = None
        was_summarized = False
        if char_count > LARGE_PAGE_CHARS:
            summary = await _summarize_large_content(full_content)
            was_summarized = summary is not None
        return FetchDocPageResult(
            summary=summary,
            content=full_content,
            url=url,
            char_count=char_count,
            was_summarized=was_summarized,
        )
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


def _grep_filter_content(
    content: str,
    pattern: str,
    case_sensitive: bool = False,
    context_lines: int = 1,
    max_matches: int = 50,
) -> GrepFilterResult:
    """Filter documentation content by grep-like pattern. Returns Pydantic struct."""
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

    parts = [f"## Grep results for pattern `{pattern}` ({len(matches)} matches)\n"]
    for sect, lines_list in section_matches.items():
        clean_title = (
            re.sub(r"^#+\s*", "", sect) if sect and sect != "(no section)" else sect or "(no section)"
        )
        parts.append(f"### {clean_title}\n")
        parts.extend(lines_list)
        parts.append("")

    formatted_section = "\n".join(parts).strip()

    return GrepFilterResult(
        pattern=pattern,
        total_matches=len(matches),
        matches=matches[:max_matches],
        formatted_section=formatted_section,
    )


@mcp.tool()
def filter_grep(
    content: str,
    pattern: str,
    case_sensitive: bool = False,
    context_lines: int = 1,
    max_matches: int = 50,
) -> GrepFilterResult:
    """Filter documentation content by grep-like pattern. Returns structured result.

    Use this after ibm_search_doc to narrow down specific lines (e.g. config keys,
    error codes, API endpoints). Supports regex patterns.

    Examples:
        filter_grep(content, r"timeout|retry")               # timeout or retry
        filter_grep(content, r"^\s*- ", max_matches=20)      # bullet points
        filter_grep(content, r"Error \d+")                   # Error 123, Error 456
        filter_grep(content, r"api_key|API_KEY")             # auth-related

    Args:
        content: Documentation markdown (e.g. from ibm_search_doc).
        pattern: Regex pattern to match (e.g. "timeout", r"Error \\d+").
        case_sensitive: If False, match case-insensitively.
        context_lines: Lines of context around each match (0-3).
        max_matches: Stop after this many matches (1-100).

    Returns:
        GrepFilterResult with matches, line numbers, sections, and formatted markdown.
    """
    context_lines = max(0, min(3, context_lines))
    max_matches = max(1, min(100, max_matches))
    return _grep_filter_content(content, pattern, case_sensitive, context_lines, max_matches)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = getattr(settings.server_ports, "docs_mcp", 8113)
    mcp.run(transport="sse", host="127.0.0.1", port=port)

# Made with Bob
