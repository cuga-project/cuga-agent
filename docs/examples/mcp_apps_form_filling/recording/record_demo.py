"""Record the real CUGA MCP Apps form-filling flow as a video, with caption timestamps.

Drives the live CUGA UI (localhost:7860) in the installed Google Chrome with Playwright,
records the page in real time, and writes captions.json (start time, text) next to the video.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1440, 860
QUERY = "Create a customers table to store name, email and signup date."

CURSOR_JS = """
if (window.top === window) {
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const c = document.createElement('div');
    c.id = '__demo_cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l15 11-6.5 1.2L16 21l-2.7 1.2-3-6.6L5 20z" fill="#111" stroke="#fff" stroke-width="1.5"/></svg>';
    Object.assign(c.style, {position:'fixed', left:'720px', top:'430px', zIndex:2147483647, pointerEvents:'none',
      transition:'left .55s ease, top .55s ease', filter:'drop-shadow(0 1px 2px rgba(0,0,0,.4))'});
    const ring = document.createElement('div');
    ring.id = '__demo_ring';
    Object.assign(ring.style, {position:'fixed', width:'34px', height:'34px', borderRadius:'50%', border:'3px solid #ff832b',
      zIndex:2147483646, pointerEvents:'none', opacity:'0', transform:'translate(-50%,-50%) scale(.4)', transition:'opacity .35s, transform .35s'});
    document.body.append(ring, c);
    window.__demoMove = (x, y) => { c.style.left = x + 'px'; c.style.top = y + 'px'; };
    window.__demoPulse = (x, y) => {
      ring.style.transition = 'none'; ring.style.left = x + 'px'; ring.style.top = y + 'px';
      ring.style.opacity = '1'; ring.style.transform = 'translate(-50%,-50%) scale(.4)';
      requestAnimationFrame(() => { ring.style.transition = 'opacity .45s, transform .45s'; ring.style.opacity = '0'; ring.style.transform = 'translate(-50%,-50%) scale(1.6)'; });
    };
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
}
"""


async def main():
    captions: list[tuple[float, str]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome", headless=True, args=[f"--window-size={W},{H}", "--hide-scrollbars"]
        )
        ctx = await browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(OUT),
            record_video_size={"width": W, "height": H},
            device_scale_factor=1,
            screen={"width": W, "height": H},
        )
        await ctx.add_init_script(CURSOR_JS)
        page = await ctx.new_page()
        t0 = time.monotonic()

        def caption(text: str):
            t = time.monotonic() - t0
            captions.append((round(t, 2), text))
            print(f"[{t:6.1f}s] {text}", flush=True)

        async def pause(s: float):
            await page.wait_for_timeout(int(s * 1000))

        async def point(locator, click=True):
            await locator.scroll_into_view_if_needed()
            box = await locator.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            await page.evaluate("([x,y]) => window.__demoMove && window.__demoMove(x,y)", [x, y])
            await pause(0.7)
            if click:
                await page.evaluate("([x,y]) => window.__demoPulse && window.__demoPulse(x,y)", [x, y])
                await locator.click()
                await pause(0.3)

        async def wheel(total: int, step: int = 120):
            await page.mouse.move(W * 0.55, H * 0.5)
            for _ in range(max(1, abs(total) // step)):
                await page.mouse.wheel(0, step if total > 0 else -step)
                await pause(0.12)

        await page.goto("http://localhost:7860/chat", wait_until="networkidle")
        composer = page.locator("textarea.cds-aichat--input-textarea")
        await composer.wait_for(timeout=60000)
        await pause(0.5)
        caption(
            "This is the live CUGA chat UI (localhost:7860). CUGA is connected to a deterministic "
            "'table designer' MCP server built on the MCP Apps standard (mcpui.dev)."
        )
        await pause(5)

        caption("The user asks CUGA to create a table.")
        await point(composer)
        await page.keyboard.type(QUERY, delay=45)
        await pause(0.8)
        await page.keyboard.press("Enter")
        await pause(1.0)
        caption(
            "CUGA's agent (the LLM) plans the task and calls the MCP tool "
            "propose_table(table_name, purpose, fields)... (real time: waiting for the LLM)"
        )

        frame_el = page.locator(".cuga-mcp-app__frame iframe")
        await frame_el.wait_for(timeout=180000)
        form = page.frame_locator(".cuga-mcp-app__frame iframe").frame_locator("iframe")
        await form.get_by_role("heading", level=1).wait_for(timeout=60000)
        await pause(0.8)
        caption(
            "propose_table declares a UI resource (ui://table-designer/form). CUGA pauses the agent "
            "and renders the tool's form inside the chat, in a sandboxed iframe."
        )
        await point(form.locator("h1"), click=False)
        await pause(5.5)

        name_input = form.locator('input[data-row="1"]')
        old_name = await name_input.input_value()
        caption(
            "The form shows the columns the tool proposed from CUGA's inputs. "
            f"The user renames the column '{old_name}' to 'full_name'..."
        )
        await point(name_input)
        await name_input.press("Meta+a")
        await page.keyboard.type("full_name", delay=70)
        await pause(1.2)

        caption("...and adds a new column, 'phone'. Renamed and new rows are marked in the form.")
        await point(form.get_by_role("button", name="Add column"))
        await page.keyboard.press("Meta+a")
        await page.keyboard.type("phone", delay=80)
        await pause(2.5)

        caption(
            "'Continue to review' sends the edits to the tool (submit_table_design, an app-only tool) "
            "through CUGA. The tool validates them..."
        )
        await point(form.get_by_role("button", name="Continue to review"))
        await form.get_by_role("heading", name="Accept the design", exact=False).wait_for(timeout=30000)
        await pause(1.0)
        caption("...and re-presents a second form: the changes from its proposal and the generated SQL.")
        await point(form.get_by_role("heading", name="Accept the design", exact=False), click=False)
        await pause(3)
        await point(form.get_by_text("Changes from the proposal"), click=False)
        await pause(3.5)
        await point(form.get_by_role("heading", name="Generated SQL"), click=False)
        await pause(3.5)

        caption(
            "The user accepts. confirm_table_design returns the approved design, and the form "
            "hands it back to CUGA (ui/update-model-context + ui/message)."
        )
        async with page.expect_response(
            lambda r: r.url.endswith("/stream") and r.request.method == "POST", timeout=60000
        ) as resp_info:
            await point(form.get_by_role("button", name="Accept design"))
        await pause(1.5)
        caption(
            "CUGA resumes the agent. The approved design is returned as the result of the "
            "original propose_table call."
        )
        resp = await resp_info.value
        await pause(3)
        caption(
            "The agent continues and calls the next tool, create_table, with exactly the approved columns..."
        )
        await resp.finished()
        await pause(1.0)

        caption("CUGA's final answer: the table was created with the user's edits (full_name, phone).")
        await wheel(1800)
        await pause(4)
        details = page.get_by_text("Show details").last
        if await details.count():
            caption(
                "Expanding the agent's steps: the code it ran calls create_table with the approved columns."
            )
            await point(details)
            await pause(1.0)
            # Keyboard: from the toggle, Tab to the first step and open it.
            await page.keyboard.press("Tab")
            await pause(0.4)
            await page.keyboard.press("Enter")
            await pause(5.5)
        caption(
            "Recorded in real time against the running CUGA server. The tools are deterministic; "
            "only the agent's planning uses the LLM."
        )
        await pause(5)
        caption("")
        video_path = await page.video.path()
        await ctx.close()
        await browser.close()

    (OUT / "captions.json").write_text(json.dumps({"video": str(video_path), "captions": captions}, indent=2))
    print("VIDEO", video_path)


asyncio.run(main())
