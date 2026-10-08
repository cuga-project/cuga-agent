const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const board = document.querySelector("#board");
const kpis = document.querySelector("#kpis");
const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-input");
const send = document.querySelector("#send");
let view = "pipeline";
let threadId = sessionStorage.getItem("harborline-thread") || "";

document.querySelectorAll(".views button").forEach((button) => {
  button.addEventListener("click", () => {
    view = button.dataset.view;
    document.querySelectorAll(".views button").forEach((item) => item.classList.toggle("active", item === button));
    refresh();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addMessage(message, "user");
  send.disabled = true;
  const pending = addMessage("Checking the book…", "assistant");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, thread_id: threadId || null }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Chat failed");
    threadId = body.thread_id || threadId;
    if (threadId) sessionStorage.setItem("harborline-thread", threadId);
    pending.querySelector("p").textContent = readable(body.error || body.answer || "No answer returned.");
    const calls = body.tool_calls || [];
    if (calls.length) {
      const tools = document.createElement("div");
      tools.className = "tools";
      calls.forEach((call) => {
        const chip = document.createElement("span");
        chip.textContent = call.name;
        tools.appendChild(chip);
      });
      pending.appendChild(tools);
    }
    await refresh();
  } catch (error) {
    pending.querySelector("p").textContent = error.message;
  } finally {
    send.disabled = false;
    input.focus();
  }
});

function readable(text) {
  return String(text).replace(/\*\*/g, "");
}

function addMessage(text, role) {
  const article = document.createElement("article");
  article.className = `bubble ${role}`;
  const paragraph = document.createElement("p");
  paragraph.textContent = readable(text);
  article.appendChild(paragraph);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
  return article;
}

async function refresh() {
  const [summary, opportunities, accounts, leads, contacts] = await Promise.all([
    getJSON("/api/summary"),
    getJSON("/api/opportunities"),
    getJSON("/api/accounts"),
    getJSON("/api/leads"),
    getJSON("/api/contacts"),
  ]);
  renderKpis(summary);
  if (view === "pipeline") renderPipeline(summary.stages, opportunities);
  if (view === "accounts") renderAccounts(accounts, opportunities);
  if (view === "leads") renderLeads(leads);
  if (view === "contacts") renderContacts(contacts);
}

function renderKpis(summary) {
  const items = [
    ["Open pipeline", money.format(summary.open_pipeline)],
    ["Open deals", String(summary.open_deal_count)],
    ["Won", money.format(summary.won_value)],
    ["Win rate", `${Math.round(summary.win_rate * 100)}%`],
  ];
  kpis.replaceChildren();
  items.forEach(([label, value]) => {
    const card = document.createElement("article");
    card.className = "kpi";
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    card.append(span, strong);
    kpis.appendChild(card);
  });
}

function renderPipeline(stages, opportunities) {
  const wrap = document.createElement("div");
  wrap.className = "pipeline";
  stages.forEach((stage) => {
    const column = document.createElement("section");
    column.className = "column";
    const title = document.createElement("h2");
    title.textContent = stage;
    column.appendChild(title);
    opportunities.filter((deal) => deal.stage === stage).forEach((deal) => {
      const card = document.createElement("article");
      card.className = "deal";
      const name = document.createElement("b");
      name.textContent = deal.name;
      const account = document.createElement("small");
      account.textContent = deal.account_name;
      const value = document.createElement("div");
      value.className = "money";
      value.textContent = money.format(deal.value);
      card.append(name, account, value);
      column.appendChild(card);
    });
    wrap.appendChild(column);
  });
  board.replaceChildren(wrap);
}

function renderAccounts(accounts, opportunities) {
  const wrap = document.createElement("div");
  wrap.className = "cards";
  accounts.forEach((account) => {
    const open = opportunities.filter((deal) => deal.account_name === account.name && !deal.stage.startsWith("closed"));
    const card = document.createElement("article");
    card.className = "account";
    const title = document.createElement("h3");
    title.textContent = account.name;
    const meta = document.createElement("p");
    meta.textContent = `${account.industry} · ${account.city}`;
    const owner = document.createElement("p");
    owner.textContent = `Owner ${account.owner}`;
    const deals = document.createElement("p");
    deals.textContent = `${open.length} open · ${money.format(account.annual_revenue)} revenue`;
    card.append(title, meta, owner, deals);
    wrap.appendChild(card);
  });
  board.replaceChildren(wrap);
}

function renderLeads(leads) {
  board.replaceChildren(table(
    ["Name", "Company", "Status", "Score", "Source"],
    leads.map((lead) => [ `${lead.first_name} ${lead.last_name}`, lead.company, lead.status, String(lead.score), lead.source ]),
  ));
}

function renderContacts(contacts) {
  board.replaceChildren(table(
    ["Name", "Account", "Title", "Email"],
    contacts.map((person) => [ `${person.first_name} ${person.last_name}`, person.account_name, person.title, person.email ]),
  ));
}

function table(headers, rows) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const element = document.createElement("table");
  const head = document.createElement("tr");
  headers.forEach((header) => {
    const cell = document.createElement("th");
    cell.textContent = header;
    head.appendChild(cell);
  });
  element.appendChild(head);
  rows.forEach((row) => {
    const line = document.createElement("tr");
    row.forEach((value, index) => {
      const cell = document.createElement("td");
      if (headers[index] === "Status") {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = value;
        cell.appendChild(pill);
      } else {
        cell.textContent = value;
      }
      line.appendChild(cell);
    });
    element.appendChild(line);
  });
  wrap.appendChild(element);
  return wrap;
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not load ${url}`);
  return response.json();
}

refresh().catch((error) => {
  board.textContent = error.message;
});
