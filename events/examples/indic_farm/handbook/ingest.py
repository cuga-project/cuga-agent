# ingest.py
#
# One-time setup step: parses a PDF (via CUGA's Docling-based knowledge
# engine) and builds the local vector store this app searches at runtime.
#
# Usage:
#   python ingest.py path/to/your_handbook.pdf
#
# This only needs to be run once per document. The resulting knowledge
# base is stored under .cuga/knowledge/ (see cuga_folder in main.py) and
# is reused automatically on every subsequent run of the app.

import sys
import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DIR = Path(__file__).parent

import cuga_compat  # noqa: E402  — must run BEFORE `from cuga import …` (it sets env)

cuga_compat.configure(_DIR)

from cuga import CugaAgent  # noqa: E402


async def main():
    if len(sys.argv) != 2:
        print("Usage: python ingest.py path/to/your_document.pdf")
        sys.exit(1)

    pdf_path = Path(sys.argv[1]).resolve()
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    # cuga_folder must match the one main.py uses, so the app finds the
    # same knowledge base this script builds.
    agent = CugaAgent(enable_knowledge=True, cuga_folder=str(_DIR / ".cuga"))

    print(f"Starting ingestion of {pdf_path.name} ...")
    # scope="agent" = permanent, available across all future runs/sessions
    # (as opposed to scope="session", which would forget it after one conversation)
    await agent.knowledge.ingest(str(pdf_path), scope="agent")
    print("Ingestion complete.")

    docs = await agent.knowledge.list_documents()
    print("Documents in knowledge base:", docs)

    await agent.aclose()
    # CUGA logs a failed ingest and returns normally, so check the result.
    if not any(d.get("status") == "indexed" for d in docs):
        sys.exit("Ingestion failed: nothing was indexed (see the ERROR line above).")


if __name__ == "__main__":
    asyncio.run(main())
