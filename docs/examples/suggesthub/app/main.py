from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from docs.examples.suggesthub.agents.bob_agent import get_bob_agent
from docs.examples.suggesthub.app import repository
from docs.examples.suggesthub.app.database import db_session, init_db
from docs.examples.suggesthub.app.seed import reset_seeded_database, seed_database
from docs.examples.suggesthub.tools.suggesthub_tools import deterministic_intake

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class BobMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str = "suggesthub-demo"
    visitor_id: str = "anonymous-demo"


class PublishDraftRequest(BaseModel):
    draft_id: int
    author_name: str = "Anonymous IBMer"


class VoteRequest(BaseModel):
    visitor_id: str = Field(min_length=1)


class StatusRequest(BaseModel):
    suggestion_id: int
    status: str
    response: str
    manager_email: str
    manager_name: str = "Facilities Manager"


class DraftResponseRequest(BaseModel):
    suggestion_id: int
    intended_status: str = "Under Review"
    manager_email: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with db_session() as conn:
        seed_database(conn)
    yield


app = FastAPI(
    title="IBM SuggestHub CUGA Prototype",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _status_class(status: str) -> str:
    if status == "Resolved":
        return "resolved"
    if status == "In Progress":
        return "progress"
    if status == "Declined":
        return "declined"
    return ""


def _suggestion_card(item: dict) -> str:
    title = escape(item["title"])
    description = escape(item["description"])
    impact = escape(item["impact"])
    category = escape(item["category"])
    location = escape(item["location"])
    status = escape(item["status"])
    return f"""
    <article class="card">
      <div class="meta">
        <span class="pill status {_status_class(item['status'])}">{status}</span>
        <span class="pill">{category}</span>
        <span class="pill">{location}</span>
        <span class="pill">#{item['id']}</span>
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      <p class="muted"><strong>Impact:</strong> {impact}</p>
      <form hx-post="/partials/suggestions/{item['id']}/vote" hx-target="#suggestions" hx-swap="innerHTML" class="inline-form">
        <input type="hidden" name="visitor_id" data-visitor-id />
        <input type="hidden" name="location" data-filter="location" />
        <input type="hidden" name="category" data-filter="category" />
        <input type="hidden" name="status" data-filter="status" />
        <button class="vote" type="submit">{item['vote_count']} votes</button>
      </form>
    </article>
    """


def _suggestions_html(items: list[dict]) -> str:
    if not items:
        return '<div class="empty">No suggestions match these filters.</div>'
    return "\n".join(_suggestion_card(item) for item in items)


def _manager_summary_html(summary: dict) -> str:
    counts = summary.get("status_counts", {})
    statuses = ["New", "Under Review", "In Progress", "Resolved", "Declined"]
    return "\n".join(
        f'<div class="stat"><strong>{int(counts.get(status, 0))}</strong>{escape(status)}</div>' for status in statuses
    )


def _story_html(story: dict) -> str:
    updates = "\n".join(
        f"<li><strong>{escape(update['status'])}</strong><br>{escape(update['response'])}</li>"
        for update in story.get("updates", [])
    )
    return f"""
    <div class="meta">
      <span class="pill status resolved">{escape(story['status'])}</span>
      <span class="pill">{story['vote_count']} votes</span>
      <span class="pill">{escape(story['location'])}</span>
    </div>
    <h3>{escape(story['title'])}</h3>
    <p>{escape(story['description'])}</p>
    <p><strong>Why it mattered:</strong> {escape(story['impact'])}</p>
    <ul class="timeline">{updates}</ul>
    """


def _oob_refresh_fragments(include_manager: bool = False) -> str:
    with db_session() as conn:
        suggestions_html = _suggestions_html(repository.list_suggestions(conn, sort="trending"))
        resolved = repository.list_suggestions(conn, status="Resolved", sort="votes")
        story_html = _story_html(resolved[0]) if resolved else ""
        manager_html = _manager_summary_html(repository.manager_summary(conn)) if include_manager else ""
    manager_fragment = (
        f'<div id="statusSummary" class="status-grid" hx-swap-oob="innerHTML">{manager_html}</div>'
        if include_manager
        else ""
    )
    return f"""
    <div id="suggestions" class="cards" hx-swap-oob="innerHTML">{suggestions_html}</div>
    {manager_fragment}
    <article id="storyCard" class="story" hx-swap-oob="innerHTML">{story_html}</article>
    """


def _bob_result_html(reply: str, matches: list[dict], draft: dict | None) -> str:
    match_html = ""
    if matches:
        match_html = "\n".join(
            f"""
            <div class="match">
              <strong>Possible duplicate: {escape(match['title'])}</strong>
              <p>{match['vote_count']} votes · {escape(match['location'])} · {round(match['similarity'] * 100)}% match</p>
              <form hx-post="/partials/suggestions/{match['id']}/vote" hx-target="#suggestions" hx-swap="innerHTML" class="inline-form">
                <input type="hidden" name="visitor_id" data-visitor-id />
                <button type="submit">Upvote existing</button>
              </form>
            </div>
            """
            for match in matches
        )

    draft_html = ""
    if draft:
        draft_html = f"""
        <div class="draft">
          <h3>Draft suggestion</h3>
          <p><strong>{escape(draft['title'])}</strong></p>
          <p>{escape(draft['description'])}</p>
          <div class="meta">
            <span class="pill">{escape(draft['category'])}</span>
            <span class="pill">{escape(draft['location'])}</span>
          </div>
          <p><strong>Impact:</strong> {escape(draft['impact'])}</p>
          <form hx-post="/partials/bob/publish" hx-target="#bobResult" hx-swap="innerHTML">
            <input type="hidden" name="draft_id" value="{draft['id']}" />
            <input type="hidden" name="author_name" value="Anonymous IBMer" />
            <button type="submit">Publish draft</button>
          </form>
        </div>
        """
    return f'<p class="bob-reply">{escape(reply)}</p>{match_html}{draft_html}'


def _validate_manager_email(email: str | None) -> str:
    value = (email or "").strip().lower()
    if not value.endswith("@ibm.com") or value == "@ibm.com":
        raise HTTPException(status_code=403, detail="Manager view requires an @ibm.com email.")
    return value


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manager")
async def manager_page():
    return FileResponse(STATIC_DIR / "manager.html")


@app.post("/api/bob/message")
async def bob_message(payload: BobMessageRequest) -> dict[str, Any]:
    fallback = deterministic_intake(payload.message, payload.thread_id)
    try:
        result = await get_bob_agent().invoke(
            payload.message,
            thread_id=payload.thread_id,
        )
        answer = getattr(result, "answer", None) or str(result)
        if getattr(result, "error", None):
            answer = fallback["reply"]
    except Exception:
        answer = fallback["reply"]

    with db_session() as conn:
        draft = repository.latest_draft(conn, payload.thread_id) or fallback["draft"]
    return {
        "reply": answer,
        "draft": draft,
        "matches": fallback["matches"],
    }


@app.post("/api/bob/publish")
async def publish_draft(payload: PublishDraftRequest) -> dict:
    try:
        with db_session() as conn:
            return repository.publish_draft(conn, payload.draft_id, payload.author_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/suggestions")
async def suggestions(
    location: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort: str = Query("votes", pattern="^(votes|newest|status|trending)$"),
) -> list[dict]:
    with db_session() as conn:
        return repository.list_suggestions(conn, location=location, category=category, status=status, sort=sort)


@app.get("/api/suggestions/{suggestion_id}")
async def suggestion(suggestion_id: int) -> dict:
    with db_session() as conn:
        item = repository.get_suggestion(conn, suggestion_id)
    if not item:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return item


@app.post("/api/suggestions/{suggestion_id}/vote")
async def vote_suggestion(suggestion_id: int, payload: VoteRequest) -> dict:
    try:
        with db_session() as conn:
            return repository.vote(conn, suggestion_id, payload.visitor_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/manager/summary")
async def manager_summary(location: str | None = None, manager_email: str | None = None) -> dict:
    _validate_manager_email(manager_email)
    with db_session() as conn:
        return repository.manager_summary(conn, location=location)


@app.post("/api/manager/status")
async def manager_status(payload: StatusRequest) -> dict:
    _validate_manager_email(payload.manager_email)
    try:
        with db_session() as conn:
            return repository.update_status(
                conn,
                suggestion_id=payload.suggestion_id,
                status=payload.status,
                response=payload.response,
                manager_name=payload.manager_name,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/manager/draft-response")
async def manager_draft_response(payload: DraftResponseRequest) -> dict:
    _validate_manager_email(payload.manager_email)
    with db_session() as conn:
        suggestion = repository.get_suggestion(conn, payload.suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    prompt = (
        "Draft a concise, public manager response for this SuggestHub item. "
        f"Title: {suggestion['title']}. Location: {suggestion['location']}. "
        f"Impact: {suggestion['impact']}. Intended status: {payload.intended_status}."
    )
    fallback = (
        f"Thanks for raising '{suggestion['title']}'. We reviewed the impact for "
        f"{suggestion['location']} and are moving this to {payload.intended_status}. "
        "We will post the next update when the owner and timing are confirmed."
    )
    try:
        result = await get_bob_agent().invoke(prompt, thread_id=f"manager-{payload.suggestion_id}")
        draft = getattr(result, "answer", None) or fallback
        if getattr(result, "error", None):
            draft = fallback
    except Exception:
        draft = fallback
    return {"suggestion": suggestion, "draft_response": draft}


@app.get("/api/story/resolved")
async def resolved_story() -> dict:
    with db_session() as conn:
        items = repository.list_suggestions(conn, status="Resolved", sort="votes")
    if not items:
        raise HTTPException(status_code=404, detail="No resolved story available")
    return items[0]


@app.post("/api/demo/reset")
async def reset_demo() -> dict:
    reset_seeded_database()
    return {"ok": True}


@app.get("/partials/suggestions", response_class=HTMLResponse)
async def suggestions_partial(
    location: str | None = None,
    category: str | None = None,
    status: str | None = None,
    sort: str = Query("trending", pattern="^(votes|newest|status|trending)$"),
) -> str:
    with db_session() as conn:
        items = repository.list_suggestions(conn, location=location, category=category, status=status, sort=sort)
    return _suggestions_html(items)


@app.post("/partials/suggestions/{suggestion_id}/vote", response_class=HTMLResponse)
async def vote_suggestion_partial(suggestion_id: int, request: Request) -> str:
    form = await request.form()
    visitor_id = str(form.get("visitor_id") or "anonymous-demo")
    location = str(form.get("location") or "") or None
    category = str(form.get("category") or "") or None
    status = str(form.get("status") or "") or None
    try:
        with db_session() as conn:
            repository.vote(conn, suggestion_id, visitor_id)
            items = repository.list_suggestions(conn, location=location, category=category, status=status, sort="trending")
            suggestions_html = _suggestions_html(items)
            resolved = repository.list_suggestions(conn, status="Resolved", sort="votes")
            story_html = _story_html(resolved[0]) if resolved else ""
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return f"""
    {suggestions_html}
    <article id="storyCard" class="story" hx-swap-oob="innerHTML">{story_html}</article>
    """


@app.post("/partials/bob/message", response_class=HTMLResponse)
async def bob_message_partial(request: Request) -> str:
    form = await request.form()
    message = str(form.get("message") or "").strip()
    thread_id = str(form.get("thread_id") or "suggesthub-demo")
    if not message:
        return '<p class="error">Tell Bob what IBM should improve.</p>'
    result = await bob_message(BobMessageRequest(message=message, thread_id=thread_id))
    return _bob_result_html(result["reply"], result["matches"], result["draft"])


@app.post("/partials/bob/publish", response_class=HTMLResponse)
async def publish_draft_partial(request: Request) -> str:
    form = await request.form()
    draft_id = int(str(form.get("draft_id") or "0"))
    author_name = str(form.get("author_name") or "Anonymous IBMer")
    try:
        with db_session() as conn:
            suggestion = repository.publish_draft(conn, draft_id, author_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return f"""
    <p class="success">Published #{suggestion['id']} to the Public Hub.</p>
    {_oob_refresh_fragments()}
    """


@app.get("/partials/manager/summary", response_class=HTMLResponse)
async def manager_summary_partial(location: str | None = None, manager_email: str | None = None) -> str:
    _validate_manager_email(manager_email)
    with db_session() as conn:
        summary = repository.manager_summary(conn, location=location)
    return _manager_summary_html(summary)


@app.post("/partials/manager/draft-response", response_class=HTMLResponse)
async def manager_draft_response_partial(request: Request) -> str:
    form = await request.form()
    manager_email = _validate_manager_email(str(form.get("manager_email") or ""))
    suggestion_id = int(str(form.get("suggestion_id") or "0"))
    intended_status = str(form.get("status") or "Under Review")
    payload = DraftResponseRequest(
        suggestion_id=suggestion_id,
        intended_status=intended_status,
        manager_email=manager_email,
    )
    result = await manager_draft_response(payload)
    return f"""
    <label for="managerResponse">Public response</label>
    <textarea id="managerResponse" name="response" rows="4">{escape(result['draft_response'])}</textarea>
    """


@app.post("/partials/manager/status", response_class=HTMLResponse)
async def manager_status_partial(request: Request) -> str:
    form = await request.form()
    manager_email = _validate_manager_email(str(form.get("manager_email") or ""))
    payload = StatusRequest(
        suggestion_id=int(str(form.get("suggestion_id") or "0")),
        status=str(form.get("status") or "Under Review"),
        response=str(form.get("response") or ""),
        manager_email=manager_email,
        manager_name=str(form.get("manager_name") or "Demo Manager"),
    )
    await manager_status(payload)
    return f"""
    <p class="success">Status posted for suggestion #{payload.suggestion_id}.</p>
    {_oob_refresh_fragments(include_manager=True)}
    """


@app.get("/partials/story/resolved", response_class=HTMLResponse)
async def resolved_story_partial() -> str:
    story = await resolved_story()
    return _story_html(story)


@app.post("/partials/demo/reset", response_class=HTMLResponse)
async def reset_demo_partial() -> str:
    reset_seeded_database()
    return f"""
    <p class="success">Demo data reset. Open Cuga chat and try the standing desk issue again.</p>
    {_oob_refresh_fragments()}
    """
