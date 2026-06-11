"""
Playwright-based implementations of browser interaction commands.

These helpers are used when *not* running with the Chrome extension (i.e. when
`settings.advanced_features.use_extension` is False).  They directly manipulate
Playwright `Page` objects instead of communicating with the extension.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Literal, Optional

from loguru import logger
from langchain_core.runnables import RunnableConfig
from playwright.async_api import Page

from cuga.backend.browser_env.page_understanding.extractor_utils.extract_async import (
    extract_focused_element_bid,
)
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.alert import Alert

# ---------------------------------------------------------------------------
# Low-level helpers originally defined inside tools.py (copied here for
# isolation).  No extension/communicator logic – pure Playwright utilities.
# ---------------------------------------------------------------------------


async def get_elem_by_bid_async(page, bid, scroll_into_view: bool = False):  # type: ignore[unused-arg]
    if not isinstance(bid, str):
        raise ValueError(f"expected a string, got {repr(bid)}")

    current_frame = page
    i = 0
    while bid[i:] and not bid[i:].isnumeric():
        i += 1
        frame_bid = bid[:i]
        frame_elem = current_frame.get_by_test_id(frame_bid)
        if not await frame_elem.count():
            raise ValueError(f'Could not find element with bid "{bid}"')
        if scroll_into_view:
            await frame_elem.scroll_into_view_if_needed(timeout=500)
        current_frame = frame_elem.frame_locator(":scope")

    elem = current_frame.get_by_test_id(bid)
    if not await elem.count():
        raise ValueError(f'Could not find element with bid "{bid}"')
    if scroll_into_view:
        await elem.scroll_into_view_if_needed(timeout=500)
    return elem


async def add_animation(page: Page, elem: Any, icon_type: str, banner_text: str):
    """Injects purple-themed highlight & banner around the `elem`."""

    bbox = await elem.bounding_box()
    if not bbox:
        return

    await page.evaluate(
        """() => {
        if (!document.getElementById('ai-animation-styles')) {
            const style = document.createElement('style');
            style.id = 'ai-animation-styles';
            style.textContent = `@keyframes pulse{0%{opacity:0.6;transform:scale(1);}50%{opacity:1;transform:scale(1.03);}100%{opacity:0.6;transform:scale(1);}}@keyframes glowing{0%{box-shadow:0 0 3px 2px rgba(138,43,226,0.4);}50%{box-shadow:0 0 10px 5px rgba(138,43,226,0.8);}100%{box-shadow:0 0 3px 2px rgba(138,43,226,0.4);}}@keyframes rotate{0%{transform:rotate(0deg);}100%{transform:rotate(360deg);}}.ai-highlight{position:absolute;z-index:9998;border:2px solid #8a2be2;border-radius:4px;pointer-events:none;animation:glowing 1.8s infinite ease-in-out;background-color:rgba(138,43,226,0.05);}.ai-icon{position:absolute;z-index:9999;background-size:contain;background-repeat:no-repeat;width:28px;height:28px;pointer-events:none;}.ai-typing-icon{background-image:url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTcgMTJINyIgc3Ryb2tlPSIjOGEyYmUyIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjxwYXRoIGQ9Ik0xMiA3TDEyIDE3IiBzdHJva2U9IiM4YTJiZTIiIHN0cm9rZS13aWR0aD0iMiIgc3Rya2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4=");animation:pulse 1.5s infinite ease-in-out;}.ai-loading-icon{border:3px solid rgba(138,43,226,0.3);border-radius:50%;border-top:3px solid #8a2be2;animation:rotate 1s linear infinite;}.ai-success-icon{background-image:url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMjAgNkw5IDE3TDQgMTIiIHN0cm9rZT0iIzhhMmJlMiIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiLz48L3N2Zz4=");animation:pulse 1.5s infinite ease-in-out;}.ai-banner{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#9c27b0,#673ab7);color:white;padding:10px 18px;border-radius:20px;font-family:system-ui,-apple-system,sans-serif;font-size:14px;font-weight:500;z-index:10000;animation:pulse 1.5s infinite ease-in-out;pointer-events:none;box-shadow:0 3px 10px rgba(0,0,0,0.2);} .ai-focus-outline{position:absolute;z-index:9997;pointer-events:none;border-radius:4px;box-shadow:0 0 0 9999px rgba(0,0,0,0.15);} `;
            document.head.appendChild(style);
        }
    }"""
    )

    # Highlight & icon next to element
    highlight_id = f"ai-highlight-{id(elem)}"
    await page.evaluate(
        f"""(bbox) => {{
        const highlight = document.createElement('div');
        highlight.id = '{highlight_id}';
        highlight.className = 'ai-highlight';
        highlight.style.left = `${{bbox.x - 3}}px`;
        highlight.style.top = `${{bbox.y - 3}}px`;
        highlight.style.width = `${{bbox.width + 6}}px`;
        highlight.style.height = `${{bbox.height + 6}}px`;
        document.body.appendChild(highlight);

        // Create a focus outline effect (darkens the rest of the page)
        const focusOutline = document.createElement('div');
        focusOutline.id = 'ai-focus-outline-{id(elem)}';
        focusOutline.className = 'ai-focus-outline';
        focusOutline.style.left = `${{bbox.x - 5}}px`;
        focusOutline.style.top = `${{bbox.y - 5}}px`;
        focusOutline.style.width = `${{bbox.width + 10}}px`;
        focusOutline.style.height = `${{bbox.height + 10}}px`;
        document.body.appendChild(focusOutline);
    }}""",
        bbox,
    )

    # Create icon next to the element
    icon_id = f"ai-icon-{id(elem)}"
    await page.evaluate(
        f"""(bbox) => {{
        const icon = document.createElement('div');
        icon.id = '{icon_id}';
        icon.className = 'ai-icon ai-{icon_type}-icon';
        icon.style.left = `${{bbox.x + bbox.width + 8}}px`;
        icon.style.top = `${{bbox.y + (bbox.height - 28) / 2}}px`;
        document.body.appendChild(icon);
    }}""",
        bbox,
    )

    # Create banner at the bottom center
    banner_id = f"ai-banner-{id(elem)}"
    await page.evaluate(f"""() => {{
        const banner = document.createElement('div');
        banner.id = '{banner_id}';
        banner.className = 'ai-banner';
        banner.textContent = '{banner_text}';
        document.body.appendChild(banner);
    }}""")

    # Schedule removal of animations with a fade-out transition
    await page.evaluate(f"""() => {{
        setTimeout(() => {{
            const highlight = document.getElementById('{highlight_id}');
            const focusOutline = document.getElementById('ai-focus-outline-{id(elem)}');
            const icon = document.getElementById('{icon_id}');
            const banner = document.getElementById('{banner_id}');

            if (highlight) {{
                highlight.style.transition = 'opacity 0.5s ease-out';
                highlight.style.opacity = '0';
            }}
            if (focusOutline) {{
                focusOutline.style.transition = 'opacity 0.5s ease-out';
                focusOutline.style.opacity = '0';
            }}
            if (icon) {{
                icon.style.transition = 'opacity 0.5s ease-out';
                icon.style.opacity = '0';
            }}
            if (banner) {{
                banner.style.transition = 'opacity 0.5s ease-out, transform 0.5s ease-out';
                banner.style.opacity = '0';
                banner.style.transform = 'translateX(-50%) translateY(20px)';
            }}

            setTimeout(() => {{
                if (highlight) highlight.remove();
                if (focusOutline) focusOutline.remove();
                if (icon) icon.remove();
                if (banner) banner.remove();
            }}, 500);
        }}, 5000);
    }}""")


async def clear_animations(page: Page) -> None:
    """Remove any previously injected AI animation elements.

    This is robust across navigations: it removes by class selectors rather than
    element IDs that reference old handles.
    """
    try:
        await page.evaluate(
            """() => {
            const classes = [
                'ai-highlight',
                'ai-icon',
                'ai-banner',
                'ai-focus-outline',
                'ai-loading-icon',
                'ai-typing-icon',
                'ai-success-icon',
            ];
            for (const cls of classes) {
                document.querySelectorAll('.' + cls).forEach(el => el.remove());
            }
        }"""
        )
    except Exception:
        # Best-effort cleanup; ignore if the page navigated or context changed
        pass


def schedule_clear_animations(page: Page, delay_seconds: float = 6.0) -> None:
    """Schedule a delayed, best-effort cleanup so UI has time to show banner.

    This avoids removing the banner immediately while still preventing leaks
    in case timeouts fail or the page state changes unexpectedly.
    """

    async def _delayed():
        try:
            await asyncio.sleep(delay_seconds)
            await clear_animations(page)
        except Exception:
            pass

    try:
        asyncio.create_task(_delayed())
    except Exception:
        # If we cannot schedule, fallback to immediate best-effort cleanup
        # but do not await it here to avoid blocking.
        try:
            asyncio.create_task(clear_animations(page))
        except Exception:
            pass


async def check_for_alert(page: Page) -> Optional[str]:
    tab_name = await page.title()
    if "OpenStreetMap" in tab_name:
        await asyncio.sleep(1)
        alert_value = await page.evaluate("window.__last_alert")
        if alert_value:
            logger.warning(f"Dialog alert value: {alert_value}")
            await page.evaluate("window.__last_alert = null")
            return alert_value
    return None


async def _is_visible(elem: Any) -> bool:
    try:
        return await elem.is_visible(timeout=300)
    except TypeError:
        return await elem.is_visible()
    except Exception:
        return False


async def _resolve_visible_counterpart(page: Page, elem: Any) -> Any:
    """Use the visible copy when a responsive page duplicates a control."""
    if await _is_visible(elem):
        return elem

    try:
        attrs = await elem.evaluate(
            """(e) => ({
                tag: e.tagName ? e.tagName.toLowerCase() : "",
                id: e.id || "",
                name: e.getAttribute("name") || "",
                type: e.getAttribute("type") || "",
                placeholder: e.getAttribute("placeholder") || "",
                ariaLabel: e.getAttribute("aria-label") || "",
                value: e.getAttribute("value") || "",
                title: e.getAttribute("title") || ""
            })"""
        )
        handle = await page.evaluate_handle(
            """(attrs) => {
                const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                const same = (e) => {
                    if (!visible(e)) return false;
                    if (attrs.id && e.id !== attrs.id) return false;
                    if (attrs.name && e.getAttribute("name") !== attrs.name) return false;
                    if (attrs.type && e.getAttribute("type") !== attrs.type) return false;
                    if (attrs.placeholder && e.getAttribute("placeholder") !== attrs.placeholder) return false;
                    if (attrs.ariaLabel && e.getAttribute("aria-label") !== attrs.ariaLabel) return false;
                    if (attrs.value && e.getAttribute("value") !== attrs.value) return false;
                    if (attrs.title && e.getAttribute("title") !== attrs.title) return false;
                    return true;
                };
                return Array.from(document.querySelectorAll(attrs.tag || "*")).find(same) || null;
            }""",
            attrs,
        )
        candidate = handle.as_element()
        if candidate:
            logger.info("Using visible counterpart for hidden duplicated control")
            return candidate
    except Exception as exc:
        logger.debug(f"Visible counterpart lookup failed: {exc}")

    return elem


async def _should_submit_search_after_type(elem: Any) -> bool:
    try:
        return bool(
            await elem.evaluate(
                """(e) => {
                    const form = e.form;
                    const type = (e.getAttribute("type") || "").toLowerCase();
                    const name = (e.getAttribute("name") || "").toLowerCase();
                    const id = (e.id || "").toLowerCase();
                    const placeholder = (e.getAttribute("placeholder") || "").toLowerCase();
                    const inputLooksLikeSearch =
                        e.tagName && e.tagName.toLowerCase() === "input" &&
                        ["", "text", "search"].includes(type) &&
                        (["q", "query", "search"].includes(name) ||
                         ["q", "query", "search"].includes(id) ||
                         placeholder.includes("search"));
                    if (!inputLooksLikeSearch || !form) return false;
                    const action = (form.getAttribute("action") || "").toLowerCase();
                    const role = (form.getAttribute("role") || "").toLowerCase();
                    const klass = (form.getAttribute("class") || "").toLowerCase();
                    return action.includes("/search") || role === "search" || klass.includes("search");
                }"""
            )
        )
    except Exception as exc:
        logger.debug(f"Search-form submit detection failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Public command implementations
# ---------------------------------------------------------------------------


async def click_impl(
    *,
    bid: str,
    button: Literal["left", "middle", "right"] = "left",
    modifiers: Optional[List[Literal["Alt", "Control", "Meta", "Shift"]]] = None,
    config: RunnableConfig | None = None,
) -> Optional[Alert]:
    modifiers = modifiers or []
    page: Page = config.get("configurable", {}).get("page")  # type: ignore[arg-type]
    # demo_mode: str = config.get("configurable", {}).get("demo_mode", "off")

    elem = await get_elem_by_bid_async(page, bid, True)
    elem = await _resolve_visible_counterpart(page, elem)
    await add_animation(page, elem, "loading", "CUGA is clicking...")

    try:
        await elem.click(modifiers=modifiers, timeout=5000, force=True)
        alert_str = await check_for_alert(page)
        if alert_str:
            logger.warning("Returning alert value")
            return Alert(message=alert_str)
        return None
    finally:
        schedule_clear_animations(page)


async def type_impl(
    *,
    bid: str,
    value: str,
    press_enter: bool,
    config: RunnableConfig | None = None,
) -> Optional[Alert]:
    page: Page = config.get("configurable", {}).get("page")  # type: ignore[arg-type]
    demo_mode: str = config.get("configurable", {}).get("demo_mode", "off")

    elem = await get_elem_by_bid_async(page, bid, demo_mode != "off")
    elem = await _resolve_visible_counterpart(page, elem)
    await add_animation(page, elem, "typing", "CUGA is typing...")

    try:
        await elem.fill(value, timeout=1000)
        submit_search = (not press_enter) and await _should_submit_search_after_type(elem)
        if press_enter or submit_search:
            if submit_search:
                logger.info("Auto-submitting search form after typing into search input")
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass

        alert_str = await check_for_alert(page)
        if alert_str:
            logger.warning("Returning alert value")
            return Alert(message=alert_str)
        return None
    finally:
        schedule_clear_animations(page)


async def select_option_impl(
    *,
    bid: str,
    options: str | List[str],
    config: RunnableConfig | None = None,
) -> Optional[Alert]:
    page: Page = config.get("configurable", {}).get("page")  # type: ignore[arg-type]
    elem = await get_elem_by_bid_async(page, bid)
    try:
        await elem.select_option(options, timeout=500)
    except Exception:
        logger.warning("Exception – select_option failed; trying alternative paths")
        try:
            focused_bid = await extract_focused_element_bid(page)
            if focused_bid:
                elem = await get_elem_by_bid_async(page, focused_bid)
                if await elem.is_editable():
                    await elem.type(options if isinstance(options, str) else ",".join(options))
                    await page.keyboard.press("Enter")
                    return None
        except Exception:
            pass

        if await elem.is_editable():
            await elem.type(options if isinstance(options, str) else ",".join(options))
            return None
        else:
            logger.warning("select_option is not editable; falling back to click")
        await elem.click(force=True)
    return None


async def open_app_impl(*, app_name: str, config: RunnableConfig | None = None):
    page: Page = config.get("configurable", {}).get("page")  # type: ignore[arg-type]
    # Environment variables contain app URLs keyed by uppercase name
    await page.goto(getattr(__import__("os"), "environ")[app_name.upper()], timeout=30000)
    return None


async def open_dropdown_impl(*, bid: str, config: RunnableConfig | None = None):
    # Same behaviour as click but without modifiers
    return await click_impl(bid=bid, button="left", modifiers=[], config=config)


def go_back_impl(state) -> str:
    """
    Navigates to the previous page in the browser history.

    Simulates clicking the browser's back button.

    Example:
        goback()
    """
    page = state.get("page")
    page.go_back()
    return f'Navigated to previous page: {page.url}'
