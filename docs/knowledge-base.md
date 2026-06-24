# Knowledge Base

[← Back to README](../README.md)

CUGA includes a built-in knowledge base powered by LangChain and local vector stores. **Docling** is integrated for document ingestion: it parses and normalizes PDFs, Office files, HTML, Markdown, images, and other supported types before chunking and embedding, so the pipeline stays self-contained with no external document services.

When enabled, the agent can search, ingest, and manage documents.

**Try the knowledge demo:** same as the main demo but with the knowledge engine on (upload documents and query them):

```bash
cuga start demo_knowledge
```

> Walk through a full HR-Benefits demo with sample documents and example prompts:
> **[examples/knowledge_demo/](./examples/knowledge_demo)**

Knowledge is **enabled by default** via `settings.toml`. The SDK auto-injects knowledge tools
and awareness into the agent, so it knows what documents are available and how to search them.

## Programmatic Access

```python
from cuga import CugaAgent
import asyncio

agent = CugaAgent(enable_knowledge=True)

async def main():
    # Ingest a document
    await agent.knowledge.ingest("/path/to/quarterly_report.pdf")

    # The agent now automatically knows about this document
    result = await agent.invoke("What does the report say about Q4 revenue?")
    print(result.answer)  # Agent searches knowledge base and answers

    # Direct search
    results = await agent.knowledge.search("Q4 revenue figures")
    for r in results:
        print(f"{r['filename']} (page {r['page']}): {r['text'][:100]}")

    # List documents
    docs = await agent.knowledge.list_documents()

    # Clean up
    await agent.aclose()

asyncio.run(main())
```

## Session-Scoped Knowledge

Documents can be scoped to a specific conversation thread:

```python
thread_id = "user-session-123"

# Ingest into session scope (temporary, per-conversation)
await agent.knowledge.ingest("/path/to/file.pdf", scope="session", thread_id=thread_id)

# Search session documents
results = await agent.knowledge.search("query", scope="session", thread_id=thread_id)

# Agent scope (default) — permanent, shared across conversations
await agent.knowledge.ingest("/path/to/file.pdf", scope="agent")
```

## Disabling Knowledge

```python
agent = CugaAgent(tools=[my_tools], enable_knowledge=False)
```

## Supported Document Types

PDF, DOCX, XLSX, PPTX, HTML, Markdown, images, and more (via Docling).
