import asyncio
from typing import Dict, Literal, Optional

from markdownify import markdownify
from loguru import logger
from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from pydantic import BaseModel

from cuga.backend.utils.consts import EXTRACT_OBS_MAX_TRIES
from cuga.backend.browser_env.page_understanding.extractor_utils.extract_async import (
    MarkingError,
    _post_extract,
    _pre_extract,
    extract_dom_extra_properties,
    extract_dom_snapshot,
    extract_focused_element_bid,
    extract_merged_axtree,
    extract_screenshot_base64,
)
from cuga.backend.browser_env.page_understanding.nocodeui_pu_utils.model import AnalyzePageResponse
from cuga.backend.browser_env.page_understanding.nocodeui_pu_utils.nocode_utils import (
    analyze_current_page_async,
)


class PUExtracted(BaseModel):
    accessibility_tree: Optional[Dict] = None
    dom_object: Optional[Dict] = None
    focused_element_bid: Optional[str] = None
    img: Optional[str] = None
    extra_properties: Optional[Dict] = None
    nocodeui_pu: Optional[AnalyzePageResponse] = None
    page_content_as_str: Optional[str] = None
    screenshot: str


class PageUnderstandingExtractor:
    def __init__(
        self, tags_to_mark: Literal["all", "standard_html"] = "standard_html", lenient: bool = False
    ):
        self.tags_to_mark = tags_to_mark
        self.lenient = lenient

    async def extract(self, context: BrowserContext, page: Page, nocodeui_pu: bool = False) -> PUExtracted:
        dom = None
        axtree = None
        extra_properties = None
        nocodeui_pu = None
        focused_element_bid = None
        img = None
        page_content = None
        for retries_left in reversed(range(EXTRACT_OBS_MAX_TRIES)):
            try:
                # pre-extraction, mark dom elements (set bid, set dynamic attributes like value and checked)
                await _pre_extract(page, tags_to_mark=self.tags_to_mark, lenient=(retries_left == 0))
                dom = await extract_dom_snapshot(page)
                axtree = await extract_merged_axtree(page)
                focused_element_bid = await extract_focused_element_bid(page)
                extra_properties = extract_dom_extra_properties(dom)
                img = await extract_screenshot_base64(page)
                page_content = markdownify(await page.inner_html("body"))
                if nocodeui_pu:
                    nocodeui_pu = await analyze_current_page_async(context)
            except (PlaywrightError, MarkingError) as e:
                err_msg = str(e)
                # try to add robustness to async events (detached / deleted frames)
                if retries_left > 0 and (
                    "Frame was detached" in err_msg
                    or "Frame with the given frameId is not found" in err_msg
                    or "Execution context was destroyed" in err_msg
                    or "Frame has been detached" in err_msg
                    or "Cannot mark a child frame without a bid" in err_msg
                ):
                    logger.warning(
                        f"An error occurred while extracting the dom and axtree. Retrying ({retries_left}/{EXTRACT_OBS_MAX_TRIES} tries left).\n{repr(e)}"
                    )
                    # post-extract cleanup (ARIA attributes)
                    await _post_extract(page)
                    await asyncio.sleep(0.5)
                    continue
                else:
                    raise e
            break
        await _post_extract(page)
        screenshot = await extract_screenshot_base64(page)
        return PUExtracted(
            screenshot=screenshot,
            dom_object=dom,
            focused_element_bid=focused_element_bid,
            extra_properties=extra_properties,
            img=img,
            page_content_as_str=page_content,
            nocodeui_pu=nocodeui_pu,
            accessibility_tree=axtree,
        )
