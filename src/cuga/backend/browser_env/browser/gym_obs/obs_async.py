# Copyright 2024 ServiceNow
# Modifications Copyright 2025 CUGA
# Licensed under the Apache License, Version 2.0

import base64
import io
import logging
import pkgutil
import re
from typing import Literal

import numpy as np
import PIL.Image
import playwright
import playwright.async_api
from browsergym.core.observation import (
    __DATA_REGEXP,
    MarkingError,
    pop_bids_from_attribute,
)
from loguru import logger

EXTRACT_OBS_MAX_TRIES = 5
BID_ATTR = "bid"
TRANSIENT_FRAME_ERRORS = (
    "Frame was detached",
    "Frame has been detached",
    "Frame with the given frameId is not found",
    "Execution context was destroyed",
)


def _is_transient_frame_error(error: Exception) -> bool:
    return any(msg in str(error) for msg in TRANSIENT_FRAME_ERRORS)


async def _pre_extract(
    page: playwright.async_api.Page,
    tags_to_mark: Literal["all", "standard_html"] = "standard_html",
    lenient: bool = False,
):
    """
    pre-extraction routine, marks dom elements (set bid and dynamic attributes like value and checked)
    """
    js_frame_mark_elements = pkgutil.get_data(__name__, "javascript/frame_mark_elements.js").decode("utf-8")

    # we can't run this loop in JS due to Same-Origin Policy
    # (can't access the content of an iframe from a another one)
    async def mark_frames_recursive(frame, frame_bid: str):
        assert frame_bid == "" or re.match(r"^[a-z][a-zA-Z]*$", frame_bid)
        logger.debug(f"Marking frame {repr(frame_bid)}")

        # mark all DOM elements in the frame (it will use the parent frame element's bid as a prefix)
        warning_msgs = await frame.evaluate(
            js_frame_mark_elements,
            [frame_bid, BID_ATTR, tags_to_mark],
        )
        # print warning messages if any
        for msg in warning_msgs:
            logger.warning(msg)

        # recursively mark all descendant frames
        child_frames = frame.child_frames
        print(child_frames)
        for child_frame in child_frames:
            # deal with detached frames
            if await child_frame.is_detached():
                continue
            # deal with weird frames (pdf viewer in <embed>)
            child_frame_elem = await child_frame.frame_element()
            if not (await child_frame_elem.content_frame()) == child_frame:
                logger.warning(f"Skipping frame '{child_frame.name}' for marking, seems problematic.")
                continue
            # deal with sandboxed frames with blocked script execution
            sandbox_attr = await child_frame_elem.get_attribute("sandbox")
            if sandbox_attr is not None and "allow-scripts" not in sandbox_attr.split():
                continue
            child_frame_bid = await child_frame_elem.get_attribute(BID_ATTR)
            if child_frame_bid is None:
                if lenient:
                    logger.warning("Cannot mark a child frame without a bid. Skipping frame.")
                    continue
                else:
                    raise MarkingError("Cannot mark a child frame without a bid.")
            await mark_frames_recursive(child_frame, frame_bid=child_frame_bid)

    # mark all frames recursively
    await mark_frames_recursive(page.main_frame, frame_bid="")


async def _post_extract(page: playwright.async_api.Page):
    js_frame_unmark_elements = pkgutil.get_data(__name__, "javascript/frame_unmark_elements.js").decode(
        "utf-8"
    )

    # we can't run this loop in JS due to Same-Origin Policy
    # (can't access the content of an iframe from a another one)
    for frame in page.frames:
        try:
            if not frame == page.main_frame:
                # deal with weird frames (pdf viewer in <embed>)
                if not await (await frame.frame_element()).content_frame() == frame:
                    logger.warning(f"Skipping frame '{frame.name}' for unmarking, seems problematic.")
                    continue
                # deal with sandboxed frames with blocked script execution
                sandbox_attr = await (await frame.frame_element()).get_attribute("sandbox")
                if sandbox_attr is not None and "allow-scripts" not in sandbox_attr.split():
                    continue

            await frame.evaluate(js_frame_unmark_elements)
        except playwright.async_api.Error as e:
            if _is_transient_frame_error(e):
                pass
            else:
                raise e


def extract_data_items_from_aria(string: str, log_level: int = logging.NOTSET):
    """
    Utility function to extract temporary data stored in the ARIA attributes of a node
    """

    match = __DATA_REGEXP.fullmatch(string)
    if not match:
        logger.log(
            level=log_level,
            msg=f"Failed to extract BrowserGym data from ARIA string: {repr(string)}",
        )
        return [], string

    groups = match.groups()
    data_items = groups[:-1]
    original_aria = groups[-1]
    return data_items, original_aria


async def extract_all_frame_axtrees(page: playwright.async_api.Page):
    """
    Extracts the AXTree of all frames (main document and iframes) of a Playwright page using Chrome DevTools Protocol.

    Args:
        page: the playwright page of which to extract the frame AXTrees.

    Returns:
        A dictionnary of AXTrees (as returned by Chrome DevTools Protocol) indexed by frame IDs.

    """
    cdp = await page.context.new_cdp_session(page)

    # extract the frame tree
    frame_tree = await cdp.send(
        "Page.getFrameTree",
        {},
    )

    # extract all frame IDs into a list
    # (breadth-first-search through the frame tree)
    frame_ids = []
    root_frame = frame_tree["frameTree"]
    frames_to_process = [root_frame]
    while frames_to_process:
        frame = frames_to_process.pop()
        frames_to_process.extend(frame.get("childFrames", []))
        # extract the frame ID
        frame_id = frame["frame"]["id"]
        frame_ids.append(frame_id)

    # extract the AXTree of each frame
    frame_axtrees = {
        frame_id: await cdp.send(
            "Accessibility.getFullAXTree",
            {"frameId": frame_id},
        )
        for frame_id in frame_ids
    }

    await cdp.detach()

    # extract browsergym data from ARIA attributes
    for ax_tree in frame_axtrees.values():
        for node in ax_tree["nodes"]:
            data_items = []
            # look for data in the node's "roledescription" property
            if "properties" in node:
                for i, prop in enumerate(node["properties"]):
                    if prop["name"] == "roledescription":
                        data_items, new_value = extract_data_items_from_aria(prop["value"]["value"])
                        prop["value"]["value"] = new_value
                        # remove the "description" property if empty
                        if new_value == "":
                            del node["properties"][i]
                        break
            # look for data in the node's "description" (fallback plan)
            if "description" in node:
                data_items_bis, new_value = extract_data_items_from_aria(node["description"]["value"])
                node["description"]["value"] = new_value
                if new_value == "":
                    del node["description"]
                if not data_items:
                    data_items = data_items_bis
            # add the extracted "browsergym" data to the AXTree
            if data_items:
                (browsergym_id,) = data_items
                node["browsergym_id"] = browsergym_id
    return frame_axtrees


async def extract_dom_snapshot(
    page: playwright.async_api.Page,
    computed_styles=[],
    include_dom_rects: bool = True,
    include_paint_order: bool = True,
    temp_data_cleanup: bool = True,
):
    """
    Extracts the DOM snapshot of a Playwright page using Chrome DevTools Protocol.

    Args:
        page: the playwright page of which to extract the screenshot.
        computed_styles: whitelist of computed styles to return.
        include_dom_rects: whether to include DOM rectangles (offsetRects, clientRects, scrollRects) in the snapshot.
        include_paint_order: whether to include paint orders in the snapshot.
        temp_data_cleanup: whether to clean up the temporary data stored in the ARIA attributes.

    Returns:
        A document snapshot, including the full DOM tree of the root node (including iframes,
        template contents, and imported documents) in a flattened array, as well as layout
        and white-listed computed style information for the nodes. Shadow DOM in the returned
        DOM tree is flattened.

    """
    cdp = await page.context.new_cdp_session(page)
    dom_snapshot = await cdp.send(
        "DOMSnapshot.captureSnapshot",
        {
            "computedStyles": computed_styles,
            "includeDOMRects": include_dom_rects,
            "includePaintOrder": include_paint_order,
        },
    )
    await cdp.detach()

    # if requested, remove temporary data stored in the ARIA attributes of each node
    if temp_data_cleanup:
        pop_bids_from_attribute(dom_snapshot, "aria-roledescription")
        pop_bids_from_attribute(dom_snapshot, "aria-description")

    return dom_snapshot


async def extract_screenshot(page: playwright.async_api.Page):
    """
    Extracts the screenshot image of a Playwright page using Chrome DevTools Protocol.

    Args:
        page: the playwright page of which to extract the screenshot.

    Returns:
        A screenshot of the page, in the form of a 3D array (height, width, rgb).

    """

    cdp = await page.context.new_cdp_session(page)
    cdp_answer = await cdp.send(
        "Page.captureScreenshot",
        {
            "format": "png",
        },
    )
    await cdp.detach()

    # bytes of a png file
    png_base64 = cdp_answer["data"]
    png_bytes = base64.b64decode(png_base64)
    with io.BytesIO(png_bytes) as f:
        # load png as a PIL image
        img = PIL.Image.open(f)
        # convert to RGB (3 channels)
        img = img.convert(mode="RGB")
        # convert to a numpy array
        img = np.array(img)

    return img


async def extract_screenshot_base64(page: playwright.async_api.Page):
    """
    Extracts the screenshot image of a Playwright page using Chrome DevTools Protocol.

    Args:
        page: the playwright page of which to extract the screenshot.

    Returns:
        A screenshot of the page, in the form of a 3D array (height, width, rgb).

    """

    cdp = await page.context.new_cdp_session(page)
    cdp_answer = await cdp.send(
        "Page.captureScreenshot",
        {
            "format": "png",
        },
    )
    await cdp.detach()

    # bytes of a png file
    png_base64 = cdp_answer["data"]
    return png_base64


async def extract_focused_element_bid(page: playwright.async_api.Page):
    # this JS code will dive through ShadowDOMs
    extract_focused_element_with_bid_script = """\
() => {
    // This recursive function traverses shadow DOMs
    function getActiveElement(root) {
        const active_element = root.activeElement;

        if (!active_element) {
            return null;
        }

        if (active_element.shadowRoot) {
            return getActiveElement(active_element.shadowRoot);
        } else {
            return active_element;
        }
    }
    return getActiveElement(document);
}"""
    # this playwright code will dive through iFrames
    frame = page
    focused_bid = ""
    while frame:
        focused_obj = await frame.evaluate_handle(extract_focused_element_with_bid_script, BID_ATTR)
        focused_element = focused_obj.as_element()
        if focused_element:
            frame = await focused_element.content_frame()
            focused_bid = await focused_element.get_attribute(BID_ATTR)
        else:
            frame = None

    return focused_bid


async def extract_merged_axtree(page: playwright.async_api.Page):
    """
    Extracts the merged AXTree of a Playwright page (main document and iframes AXTrees merged) using Chrome DevTools Protocol.

    Args:
        page: the playwright page of which to extract the merged AXTree.

    Returns:
        A merged AXTree (same format as those returned by Chrome DevTools Protocol).

    """
    frame_axtrees = await extract_all_frame_axtrees(page)

    cdp = await page.context.new_cdp_session(page)

    # merge all AXTrees into one
    merged_axtree = {"nodes": []}
    for ax_tree in frame_axtrees.values():
        merged_axtree["nodes"].extend(ax_tree["nodes"])
        # connect each iframe node to the corresponding AXTree root node
        for node in ax_tree["nodes"]:
            if node["role"]["value"] == "Iframe":
                frame_id = (
                    cdp.send("DOM.describeNode", {"backendNodeId": node["backendDOMNodeId"]})
                    .get("node", {})
                    .get("frameId", None)
                )
                if not frame_id:
                    logger.warning(
                        f"AXTree merging: unable to recover frameId of node with backendDOMNodeId {repr(node['backendDOMNodeId'])}, skipping"
                    )
                # it seems Page.getFrameTree() from CDP omits certain Frames (empty frames?)
                # if a frame is not found in the extracted AXTrees, we just ignore it
                elif frame_id in frame_axtrees:
                    # root node should always be the first node in the AXTree
                    frame_root_node = frame_axtrees[frame_id]["nodes"][0]
                    assert frame_root_node["frameId"] == frame_id
                    node["childIds"].append(frame_root_node["nodeId"])
                else:
                    logger.warning(
                        f"AXTree merging: extracted AXTree does not contain frameId '{frame_id}', skipping"
                    )

    await cdp.detach()

    return merged_axtree
