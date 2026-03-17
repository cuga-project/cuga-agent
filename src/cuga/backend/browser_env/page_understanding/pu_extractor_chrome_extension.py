from typing import Dict, Literal, Optional, Protocol, runtime_checkable

from markdownify import markdownify
from loguru import logger
from pydantic import BaseModel

from cuga.backend.browser_env.browser.gym_obs.extract_chrome_extension import (
    ChromeExtensionCommunicator,
    ChromeExtensionError,
    full_extract_chrome_extension,
)
from cuga.backend.browser_env.browser.gym_obs.http_stream_comm import ChromeExtensionCommunicatorProtocol
from cuga.backend.browser_env.page_understanding.types.dom_tree_types import DomTreeResult


@runtime_checkable
class PUExtractedResultProtocol(Protocol):
    """
    Structural protocol for Page Understanding extraction results.
    Allows alternative result implementations to be used anywhere
    a PUExtractedChromeExtension instance is expected.
    """

    accessibility_tree: Optional[Dict]
    dom_object: Optional[Dict]
    dom_tree: Optional[DomTreeResult]
    focused_element_bid: Optional[str]
    img: Optional[str]
    extra_properties: Optional[Dict]
    nocodeui_pu: Optional[Dict]
    page_content_as_str: Optional[str]
    screenshot: str
    url: Optional[str]
    page_title: Optional[str]


@runtime_checkable
class PageUnderstandingExtractorProtocol(Protocol):
    """
    Structural protocol for Page Understanding extractors.
    Allows injection of alternative extractor implementations.
    """

    async def extract(self, nocodeui_pu: bool = False) -> PUExtractedResultProtocol: ...
    async def extract_simple(self) -> Dict: ...
    async def health_check(self) -> bool: ...


class PUExtractedChromeExtension(BaseModel):
    """
    Chrome extension-based page understanding extraction result
    """

    accessibility_tree: Optional[Dict] = None
    dom_object: Optional[Dict] = None
    dom_tree: Optional[DomTreeResult] = None
    focused_element_bid: Optional[str] = None
    img: Optional[str] = None
    extra_properties: Optional[Dict] = None
    nocodeui_pu: Optional[Dict] = None  # Not supported in Chrome extension mode
    page_content_as_str: Optional[str] = None
    screenshot: str
    url: Optional[str] = None
    page_title: Optional[str] = None


class PageUnderstandingExtractorChromeExtension:
    """
    Chrome extension-based page understanding extractor
    """

    def __init__(
        self,
        communicator: ChromeExtensionCommunicatorProtocol,
        tags_to_mark: Literal["all", "standard_html"] = "standard_html",
        lenient: bool = False,
    ):
        self.tags_to_mark = tags_to_mark
        self.lenient = lenient
        self.communicator = communicator

    async def __aenter__(self):
        """Async context manager entry"""
        self.communicator = ChromeExtensionCommunicator()
        await self.communicator.__aenter__()

        # Wait for Chrome extension to connect
        await self.communicator.wait_for_connection(timeout=15.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.communicator:
            await self.communicator.__aexit__(exc_type, exc_val, exc_tb)

    async def extract(self, nocodeui_pu: bool = False) -> PUExtractedResultProtocol:
        """
        Extract page understanding data using Chrome extension

        Args:
            context: Ignored in Chrome extension mode (kept for compatibility)
            page: Ignored in Chrome extension mode (kept for compatibility)
            nocodeui_pu: Not supported in Chrome extension mode

        Returns:
            PUExtractedChromeExtension: Extracted page data
        """
        if not self.communicator:
            raise ChromeExtensionError("Communicator not initialized. Use async context manager.")

        if nocodeui_pu:
            logger.warning("nocodeui_pu is not supported in Chrome extension mode")

        try:
            # Perform full extraction using Chrome extension
            extraction_result = await full_extract_chrome_extension(
                communicator=self.communicator,
                tags_to_mark=self.tags_to_mark,
                lenient=self.lenient,
                max_retries=3,
            )

            page_content_str = ""
            if extraction_result.get("page_content"):
                page_content_str = markdownify(extraction_result["page_content"])

            # Process DOM tree data
            dom_tree_data = extraction_result.get("dom_tree")
            dom_tree_result = None
            if dom_tree_data:
                try:
                    dom_tree_result = DomTreeResult(**dom_tree_data)
                except Exception as e:
                    logger.warning(f"Failed to parse DOM tree data: {e}")
                    dom_tree_result = None

            # Create result object
            result = PUExtractedChromeExtension(
                screenshot=extraction_result.get("screenshot", ""),
                dom_object=extraction_result.get("dom_snapshot", {}),
                dom_tree=dom_tree_result,
                focused_element_bid=extraction_result.get("focused_element_bid", ""),
                extra_properties=extraction_result.get("extra_properties", {}),
                img=extraction_result.get("screenshot", ""),  # Use screenshot as img
                page_content_as_str=page_content_str,
                nocodeui_pu=None,  # Not supported in Chrome extension mode
                accessibility_tree=extraction_result.get("accessibility_tree", {}),
                url=extraction_result.get("page_url", ""),
                page_title=extraction_result.get("page_title", ""),
            )

            return result

        except Exception as e:
            logger.error(f"Chrome extension extraction failed: {str(e)}")
            raise ChromeExtensionError(f"Extraction failed: {str(e)}")

    async def extract_simple(self) -> Dict:
        """
        Simple extraction method that returns a dictionary (for backward compatibility)

        Returns:
            Dict: Extracted data as dictionary
        """
        result = await self.extract()
        return result.model_dump()

    async def health_check(self) -> bool:
        """
        Check if the Chrome extension is responding

        Returns:
            bool: True if extension is healthy, False otherwise
        """
        if not self.communicator:
            return False

        try:
            return await self.communicator.ping()
        except Exception:
            return False
