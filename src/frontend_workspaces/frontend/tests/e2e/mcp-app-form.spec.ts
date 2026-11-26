import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect, type Locator, type Page, type Request } from "@playwright/test";
import { stub } from "./fixtures/carbon-backend";

/**
 * MCP Apps form round trip in the chat, with every backend endpoint stubbed:
 * the paused tool's form renders in the sandbox, its tools/call and model
 * context go to the interaction API, and finishing it resumes the agent.
 */

const PKG = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../cuga");
const INTERACTION = "interaction-e2e";
const THREAD = "thread-e2e";

/**
 * The MCP Apps guest SDK (``@modelcontextprotocol/ext-apps``) as a classic script. It is the copy
 * ``@mcp-ui/client`` depends on, so the lockfile pins it and no network is needed.
 */
function guestSdk(): string {
  const fromClient = createRequire(createRequire(import.meta.url).resolve("@mcp-ui/client"));
  const bundle = readFileSync(fromClient.resolve("@modelcontextprotocol/ext-apps/app-with-deps"), "utf8");
  const match = bundle.match(/export\s*\{([^}]*)\}\s*;?\s*$/);
  if (!match || match.index === undefined) throw new Error("unexpected ext-apps bundle format");
  const pairs = match[1]
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((spec) => {
      const [local, exported] = spec.split(/\s+as\s+/);
      return `${(exported || local).trim()}:${local.trim()}`;
    });
  return `${bundle.slice(0, match.index)}globalThis.McpExtApps={${pairs.join(",")}};`;
}

const APP_HTML = `<!doctype html><html><body>
<h1>E2E form</h1><p id="status">starting</p><button id="accept">Accept</button>
<script>${guestSdk().replace(/<\/script/g, "<\\/script")}</script>
<script>
  const status = document.getElementById("status");
  const app = new globalThis.McpExtApps.App({ name: "e2e-form", version: "1.0.0" }, {});
  app.ontoolresult = (r) => { status.textContent = "proposed " + r.structuredContent.columns; };
  app.connect().catch((e) => { status.textContent = "connect failed: " + e.message; });
  document.getElementById("accept").onclick = async () => {
    const r = await app.callServerTool({ name: "confirm_design", arguments: { columns: 3 } });
    status.textContent = r.content[0].text;
    await app.updateModelContext({ structuredContent: { accepted: true, columns: 3 } });
    await app.sendMessage({ role: "user", content: [{ type: "text", text: "Design accepted." }] });
  };
</script></body></html>`;

const ACTION = {
  action_id: "mcp_app_interaction",
  action_name: "Open form",
  description: "Proposed 3 columns.",
  type: "mcp_app",
  callback_url: "/stream",
  additional_data: {
    tool: {
      mcp_app: {
        interaction_id: INTERACTION,
        thread_id: THREAD,
        server: "mock_forms",
        tool: "open_form",
        agent_tool: "mock_forms_open_form",
        tool_title: "Open form",
        resource_uri: "ui://mock-forms/form",
        tool_input: { table_name: "customers" },
        tool_result: { content: [{ type: "text", text: "Proposed 3 columns." }], structuredContent: { columns: 3 } },
      },
    },
  },
};

/** Wait until the element stops moving: the app's frame resizes to its content after it loads. */
async function settled(locator: Locator): Promise<Locator> {
  let last = "";
  await expect
    .poll(async () => {
      const box = JSON.stringify(await locator.boundingBox());
      const same = box === last;
      last = box;
      return same;
    }, { intervals: [150] })
    .toBe(true);
  return locator;
}

function sse(events: [string, string][]): string {
  return events.map(([name, data]) => `event: ${name}\ndata: ${data}\n\n`).join("");
}

async function stubMcpApps(page: Page) {
  const calls: { path: string; body: any; thread: string | null }[] = [];
  const record = (request: Request) =>
    calls.push({
      path: new URL(request.url()).pathname,
      body: request.postDataJSON(),
      thread: request.headers()["x-thread-id"] ?? null,
    });
  await page.route("**/api/mcp-apps/config", (route) => route.fulfill({ json: { sandbox_origin: null } }));
  await page.route("**/api/mcp-apps/resource", (route) => {
    record(route.request());
    return route.fulfill({
      json: {
        interaction_id: INTERACTION,
        status: "pending",
        html: APP_HTML,
        csp: {},
        permissions: {},
        tool_input: ACTION.additional_data.tool.mcp_app.tool_input,
        tool_result: ACTION.additional_data.tool.mcp_app.tool_result,
      },
    });
  });
  await page.route("**/api/mcp-apps/tools/call", (route) => {
    record(route.request());
    return route.fulfill({ json: { content: [{ type: "text", text: "Design confirmed by the server." }], isError: false } });
  });
  await page.route("**/api/mcp-apps/model-context", (route) => {
    record(route.request());
    return route.fulfill({ json: {} });
  });
  // The real sandbox proxy page, served on the swapped origin (127.0.0.1 <-> localhost).
  const proxy = readFileSync(resolve(PKG, "backend/mcp_apps/sandbox_proxy.html"), "utf8");
  await page.route("**/mcp-apps/sandbox_proxy.html**", (route) => {
    const embedder = new URL(page.url()).origin;
    return route.fulfill({
      contentType: "text/html",
      body: proxy.replace("/*__ALLOWED_HOST_ORIGINS__*/[]", JSON.stringify([embedder])),
    });
  });
  return calls;
}

test("a paused tool's form renders, calls its server and resumes the agent", async ({ page }) => {
  await stub(page);
  const calls = await stubMcpApps(page);
  const streams: { body: any; capability: string | undefined }[] = [];
  await page.route("**/stream", async (route) => {
    const request = route.request();
    streams.push({ body: request.postDataJSON(), capability: request.headers()["x-cuga-mcp-apps"] });
    if (streams.length === 1) {
      await route.fulfill({
        contentType: "text/event-stream",
        body: sse([
          ["thread_created", THREAD],
          ["SuggestHumanActions", JSON.stringify(ACTION)],
        ]),
      });
    } else {
      await route.fulfill({
        contentType: "text/event-stream",
        body: sse([["Answer", "Created table customers with 3 columns."]]),
      });
    }
  });

  await page.goto("/chat");
  const composer = page.getByRole("textbox").first();
  await composer.fill("Create a customers table");
  await composer.press("Enter");

  // The web chat declares that it renders MCP Apps forms.
  await expect.poll(() => streams[0]?.capability).toBe("1");

  const widget = page.locator(".cuga-mcp-app");
  await expect(widget).toBeVisible();
  await expect(widget.getByText("Waiting for you")).toBeVisible();
  const sandbox = widget.locator("iframe").first();
  // The app runs on a different origin from the chat.
  await expect(sandbox).toHaveAttribute("src", /^http:\/\/127\.0\.0\.1:\d+\/mcp-apps\/sandbox_proxy\.html/);
  const form = page.frameLocator(".cuga-mcp-app iframe").frameLocator("iframe");
  await expect(form.getByText("proposed 3")).toBeVisible(); // the tool result reached the app
  expect(calls[0]).toMatchObject({ path: "/api/mcp-apps/resource", body: { interaction_id: INTERACTION } });

  await (await settled(form.getByRole("button", { name: "Accept" }))).click();
  await expect(form.getByText("Design confirmed by the server.")).toBeVisible();

  const toolCall = calls.find((c) => c.path === "/api/mcp-apps/tools/call");
  expect(toolCall).toMatchObject({
    body: { interaction_id: INTERACTION, name: "confirm_design", arguments: { columns: 3 } },
    thread: THREAD,
  });
  await expect.poll(() => calls.find((c) => c.path === "/api/mcp-apps/model-context")?.body).toMatchObject({
    interaction_id: INTERACTION,
    structuredContent: { accepted: true, columns: 3 },
  });

  // ui/message finishes the form and resumes the agent with the interaction's id.
  await expect.poll(() => streams.length).toBe(2);
  expect(streams[1].body).toMatchObject({
    action_id: "mcp_app_interaction",
    response_type: "mcp_app",
    metadata: { interaction_id: INTERACTION, message: "Design accepted.", cancelled: false },
  });
  await expect(widget.getByText("Submitted")).toBeVisible();
  await expect(page.getByTestId(/^message-by-index-/).getByText("Created table customers with 3 columns.")).toBeVisible();
});

test("closing the form without submitting tells the agent it was cancelled", async ({ page }) => {
  await stub(page);
  await stubMcpApps(page);
  const streams: any[] = [];
  await page.route("**/stream", async (route) => {
    streams.push(route.request().postDataJSON());
    await route.fulfill({
      contentType: "text/event-stream",
      body:
        streams.length === 1
          ? sse([
              ["thread_created", THREAD],
              ["SuggestHumanActions", JSON.stringify(ACTION)],
            ])
          : sse([["Answer", "Okay, I stopped."]]),
    });
  });

  await page.goto("/chat");
  const composer = page.getByRole("textbox").first();
  await composer.fill("Create a customers table");
  await composer.press("Enter");

  const widget = page.locator(".cuga-mcp-app");
  // The frame resizes to its content once the app loads; act on the loaded form, as a user would.
  const form = page.frameLocator(".cuga-mcp-app iframe").frameLocator("iframe");
  await expect(form.getByText("proposed 3")).toBeVisible();
  await (await settled(widget.getByRole("button", { name: "Close without submitting" }))).click();
  await expect.poll(() => streams.length).toBe(2);
  expect(streams[1]).toMatchObject({
    action_id: "mcp_app_interaction",
    metadata: { interaction_id: INTERACTION, cancelled: true },
  });
  await expect(widget.getByText("Closed")).toBeVisible();
});
