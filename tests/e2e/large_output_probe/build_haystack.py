"""Build large_output_probe fixtures: real needle facts spliced into public-domain
prose, at three sizes, so DYNACONF_LARGE_OUTPUT__ENABLED's subagent-digestion path
has something worth retrieving. See README.md for what each tier tests and measured
results.

DYNACONF_LARGE_OUTPUT__ENABLED only auto-spawns a digesting subagent when the
oversized tool-output variable is an unstructured *string* (large_output_dispatch.py's
shape check — dict/list gets a cheaper "guidance" path instead). Most real tool
outputs return structured JSON, so they rarely exercise the subagent path; these
fixtures do, by construction: a single tool returns one large block of unstructured
prose with synthetic facts ("[ARCHIVE NOTE ####]") spliced in.

Each needle is a self-contained fact that cannot be confused with anything in the
source novels, spliced in at increasing depth (`depth_pct`, 0=start, 1=end) so the 5
facts spread across the whole document. `hard` and `extreme` additionally bury each
needle among near-duplicate decoys elsewhere in the document (same sentence template,
wrong specifics) — a search that stops at the first hit on a shared keyword lands on
the wrong one; getting the right answer requires matching the question's specific
identifying detail, not just the topic.

Run:
    uv run python tests/e2e/large_output_probe/build_haystack.py [easy|hard|extreme]

Regenerating re-fetches source text over HTTPS (hard: 3 books, extreme: 7 books, ~1-3MB
each — expect extreme to take a minute or two). All three tiers' output is checked into
data/ so the probe itself never needs network access.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
START_MARKER_RE = re.compile(r"\*\*\* START OF THE PROJECT GUTENBERG EBOOK.*?\*\*\*", re.IGNORECASE)
END_MARKER_RE = re.compile(r"\*\*\* END OF THE PROJECT GUTENBERG EBOOK.*?\*\*\*", re.IGNORECASE)

# The 5 real needle facts shared by every tier, so results are comparable across
# tiers. Each has its own question and an AND-of-OR list of required substrings for
# grading (each inner list is a set of acceptable phrasings for the same fact, e.g.
# digit vs. spelled-out number — the answer must contain at least one substring from
# every inner list).
NEEDLES = [
    {
        "id": "needle_1",
        "depth_pct": 0.15,
        "text": (
            "[ARCHIVE NOTE 4471] The backup generator for Wing C was last serviced "
            "on the 14th of September by technician Priya Nandakumar."
        ),
        "question": (
            "According to the archive notes buried in the document, who serviced the "
            "backup generator for Wing C, and on what date?"
        ),
        "required_substrings": [["Priya Nandakumar"], ["14th of September", "September 14"]],
    },
    {
        "id": "needle_2",
        "depth_pct": 0.32,
        "text": (
            "[ARCHIVE NOTE 4472] Shipment consignment number KL-90231 contains "
            "seventeen crates of ceramic tiles bound for the Rotterdam depot."
        ),
        "question": (
            "What is the consignment number of the shipment of ceramic tiles bound "
            "for Rotterdam, and how many crates does it contain?"
        ),
        "required_substrings": [["KL-90231"], ["seventeen", "17"]],
    },
    {
        "id": "needle_3",
        "depth_pct": 0.50,
        "text": (
            "[ARCHIVE NOTE 4473] The quarterly safety inspection at Site 9 was "
            "signed off by inspector Marcus Whitfield, with zero violations recorded."
        ),
        "question": (
            "Who signed off the quarterly safety inspection at Site 9, and how many violations were recorded?"
        ),
        "required_substrings": [["Marcus Whitfield"], ["zero", "0"]],
    },
    {
        "id": "needle_4",
        "depth_pct": 0.68,
        "text": (
            "[ARCHIVE NOTE 4474] The replacement part for the east elevator is "
            "catalog item EL-6693-R, currently on backorder until the 3rd of November."
        ),
        "question": (
            "What is the catalog item number for the east elevator's replacement "
            "part, and when does the backorder clear?"
        ),
        "required_substrings": [["EL-6693-R"], ["3rd of November", "November 3"]],
    },
    {
        "id": "needle_5",
        "depth_pct": 0.85,
        "text": (
            "[ARCHIVE NOTE 4475] Visitor badge number VB-2207 was issued to a "
            "delegation from Helsinki, valid through the end of the month."
        ),
        "question": "What visitor badge number was issued to the delegation from Helsinki?",
        "required_substrings": [["VB-2207"]],
    },
]

# Decoys: near-duplicate notes (same template, wrong specifics) spliced in alongside
# the real needles on the `hard`/`extreme` tiers, keyed by needle id (not a flat list)
# so decoy->needle attribution survives re-sorting by char_offset. Rule: a decoy may
# repeat a detail the question already gives only if the question supplies a second,
# independent detail that still picks the real note out uniquely (needle_2's case,
# where the question gives both "ceramic tiles" and "Rotterdam"); every other needle's
# question gives only one identifying detail (Wing C, Site 9, east elevator, Helsinki),
# so no decoy may share that one detail — doing so would make the question
# unanswerable, or gradeable as PASS from the wrong note.
DECOYS_HARD = {
    "needle_1": [
        "[ARCHIVE NOTE 5502] The backup generator for Wing A was last serviced on "
        "the 2nd of March by technician Elena Popescu.",
        "[ARCHIVE NOTE 6613] The backup generator for Wing B was last serviced on "
        "the 30th of June by technician Marco Ferreira.",
    ],
    "needle_2": [
        "[ARCHIVE NOTE 5503] Shipment consignment number PL-40118 contains eleven "
        "crates of ceramic tiles bound for the Antwerp depot.",
        "[ARCHIVE NOTE 6614] Shipment consignment number KL-90455 contains nine "
        "crates of glass panels bound for the Rotterdam depot.",
    ],
    "needle_3": [
        "[ARCHIVE NOTE 5504] The quarterly safety inspection at Site 4 was signed "
        "off by inspector Grace Talbot, with two violations recorded.",
        "[ARCHIVE NOTE 6615] The quarterly safety inspection at Site 6 was signed "
        "off by inspector Diane Okafor, with one violation recorded.",
    ],
    "needle_4": [
        "[ARCHIVE NOTE 5505] The replacement part for the west elevator is catalog "
        "item EL-8842-M, currently on backorder until the 9th of January.",
        "[ARCHIVE NOTE 6616] The replacement part for the north elevator is catalog "
        "item EL-7781-Q, currently on backorder until the 12th of February.",
    ],
    "needle_5": [
        "[ARCHIVE NOTE 5506] Visitor badge number VB-3391 was issued to a "
        "delegation from Tallinn, valid through the end of the month.",
        "[ARCHIVE NOTE 6617] Visitor badge number VB-4419 was issued to a "
        "delegation from Vienna, valid through the end of the month.",
    ],
}

# Same rule as DECOYS_HARD, 4 decoys per needle instead of 2. Numbered 9001+ to stay
# visually distinct from DECOYS_HARD's own 5502-6617 notes.
DECOYS_EXTREME = {
    "needle_1": [
        "[ARCHIVE NOTE 9001] The backup generator for Wing A was last serviced on "
        "the 2nd of March by technician Elena Popescu.",
        "[ARCHIVE NOTE 9002] The backup generator for Wing B was last serviced on "
        "the 30th of June by technician Marco Ferreira.",
        "[ARCHIVE NOTE 9003] The backup generator for Wing D was last serviced on "
        "the 21st of January by technician Fatima Al-Sayed.",
        "[ARCHIVE NOTE 9004] The backup generator for Wing E was last serviced on "
        "the 5th of August by technician Tomasz Nowak.",
    ],
    "needle_2": [
        "[ARCHIVE NOTE 9005] Shipment consignment number PL-40118 contains eleven "
        "crates of ceramic tiles bound for the Antwerp depot.",
        "[ARCHIVE NOTE 9006] Shipment consignment number KL-90455 contains nine "
        "crates of glass panels bound for the Rotterdam depot.",
        "[ARCHIVE NOTE 9007] Shipment consignment number QN-11287 contains six "
        "crates of ceramic tiles bound for the Hamburg depot.",
        "[ARCHIVE NOTE 9008] Shipment consignment number RT-77820 contains four "
        "crates of marble slabs bound for the Rotterdam depot.",
    ],
    "needle_3": [
        "[ARCHIVE NOTE 9009] The quarterly safety inspection at Site 2 was signed "
        "off by inspector Omar Haddad, with three violations recorded.",
        "[ARCHIVE NOTE 9010] The quarterly safety inspection at Site 4 was signed "
        "off by inspector Grace Talbot, with two violations recorded.",
        "[ARCHIVE NOTE 9011] The quarterly safety inspection at Site 6 was signed "
        "off by inspector Diane Okafor, with one violation recorded.",
        "[ARCHIVE NOTE 9012] The quarterly safety inspection at Site 8 was signed "
        "off by inspector Liu Wei, with four violations recorded.",
    ],
    "needle_4": [
        "[ARCHIVE NOTE 9013] The replacement part for the west elevator is catalog "
        "item EL-8842-M, currently on backorder until the 9th of January.",
        "[ARCHIVE NOTE 9014] The replacement part for the north elevator is catalog "
        "item EL-7781-Q, currently on backorder until the 12th of February.",
        "[ARCHIVE NOTE 9015] The replacement part for the south elevator is catalog "
        "item EL-5510-T, currently on backorder until the 27th of March.",
        "[ARCHIVE NOTE 9016] The replacement part for the central elevator is catalog "
        "item EL-3392-V, currently on backorder until the 15th of May.",
    ],
    "needle_5": [
        "[ARCHIVE NOTE 9017] Visitor badge number VB-3391 was issued to a "
        "delegation from Tallinn, valid through the end of the month.",
        "[ARCHIVE NOTE 9018] Visitor badge number VB-4419 was issued to a "
        "delegation from Vienna, valid through the end of the month.",
        "[ARCHIVE NOTE 9019] Visitor badge number VB-5528 was issued to a "
        "delegation from Oslo, valid through the end of the month.",
        "[ARCHIVE NOTE 9020] Visitor badge number VB-6637 was issued to a "
        "delegation from Warsaw, valid through the end of the month.",
    ],
}


# Second document for the "redigest" scenario (see build_redigest_b / README): a
# completely different topic/vocabulary from NEEDLES above (IT operations / finance,
# vs. NEEDLES' facilities/logistics), deliberately, so a subagent searching this
# document with a stale task built from a *different* conversation's first message
# has essentially no shared keyword to accidentally stumble onto the right answer with
# — the fixture is meant to fail loudly under the pre-fix behavior, not by luck.
REDIGEST_B_NEEDLES = [
    {
        "id": "redigest_b1",
        "depth_pct": 0.30,
        "text": (
            "[ARCHIVE NOTE B-101] The nightly backup job for the payments database was "
            "migrated to cluster host PGDB-07 on the 22nd of April, completed by "
            "engineer Sofia Marchetti."
        ),
        "question": (
            "According to the archive notes, which cluster host was the payments "
            "database's nightly backup job migrated to, and on what date?"
        ),
        "required_substrings": [["PGDB-07"], ["22nd of April", "April 22"]],
    },
    {
        "id": "redigest_b2",
        "depth_pct": 0.70,
        "text": (
            "[ARCHIVE NOTE B-102] Budget approval code BX-8834 was granted for the Q3 "
            "marketing campaign, authorized by finance director Owen Castellano."
        ),
        "question": (
            "What budget approval code was granted for the Q3 marketing campaign, and who authorized it?"
        ),
        "required_substrings": [["BX-8834"], ["Owen Castellano"]],
    },
]


@dataclass(frozen=True)
class GutenbergBook:
    ebook_id: int
    title: str
    author: str


@dataclass(frozen=True)
class Tier:
    haystack_file: str
    questions_file: str
    sources: list[GutenbergBook]
    decoys_by_needle: dict[str, list[str]]
    # Concatenating multiple books needs a "=== SOURCE: ... ===" header to mark
    # boundaries; a single-book tier doesn't.
    add_source_headers: bool


TIERS = {
    "easy": Tier(
        haystack_file="haystack.txt",
        questions_file="questions.json",
        sources=[GutenbergBook(11, "Alice's Adventures in Wonderland", "Lewis Carroll")],
        decoys_by_needle={},
        add_source_headers=False,
    ),
    "hard": Tier(
        haystack_file="haystack_hard.txt",
        questions_file="questions_hard.json",
        sources=[
            GutenbergBook(1342, "Pride and Prejudice", "Jane Austen"),
            GutenbergBook(1661, "The Adventures of Sherlock Holmes", "Arthur Conan Doyle"),
            GutenbergBook(84, "Frankenstein", "Mary Shelley"),
        ],
        decoys_by_needle=DECOYS_HARD,
        add_source_headers=True,
    ),
    "extreme": Tier(
        haystack_file="haystack_extreme.txt",
        questions_file="questions_extreme.json",
        # Deliberately disjoint from "hard"'s sources, so this fixture isn't just
        # hard's text repeated with more padding.
        sources=[
            GutenbergBook(2701, "Moby-Dick; or, The Whale", "Herman Melville"),
            GutenbergBook(1400, "Great Expectations", "Charles Dickens"),
            GutenbergBook(98, "A Tale of Two Cities", "Charles Dickens"),
            GutenbergBook(76, "Adventures of Huckleberry Finn", "Mark Twain"),
            GutenbergBook(345, "Dracula", "Bram Stoker"),
            GutenbergBook(996, "Don Quixote", "Miguel de Cervantes Saavedra"),
            GutenbergBook(145, "Middlemarch", "George Eliot"),
        ],
        decoys_by_needle=DECOYS_EXTREME,
        add_source_headers=True,
    ),
}


REDIGEST_B_SOURCE = GutenbergBook(120, "Treasure Island", "Robert Louis Stevenson")


def fetch_book_text(ebook_id: int) -> str:
    """Fetch one Gutenberg ebook's plain text and strip its boilerplate header/footer.

    Uses `curl` rather than `urllib`: in at least one dev environment, Python's own
    OpenSSL cert store rejects gutenberg.org's chain while curl (system/keychain trust
    store) accepts it fine.
    """
    url = f"https://www.gutenberg.org/cache/epub/{ebook_id}/pg{ebook_id}.txt"
    result = subprocess.run(
        ["curl", "-sL", "--fail", "--max-time", "60", url], capture_output=True, check=True
    )
    raw = result.stdout.decode("utf-8", errors="replace")

    start = START_MARKER_RE.search(raw)
    end = END_MARKER_RE.search(raw)
    if not start or not end:
        raise RuntimeError(f"Could not find Project Gutenberg START/END markers for ebook {ebook_id}")
    return raw[start.end() : end.start()].strip()


def build_source_text(tier: Tier) -> str:
    if not tier.add_source_headers:
        return fetch_book_text(tier.sources[0].ebook_id)

    parts = []
    for book in tier.sources:
        text = fetch_book_text(book.ebook_id)
        parts.append(
            f"=== SOURCE: {book.title} by {book.author} (Project Gutenberg #{book.ebook_id}) ===\n\n{text}"
        )
        print(f"  fetched #{book.ebook_id} {book.title!r}: {len(text):,} chars")
    return "\n\n".join(parts)


def check_no_decoy_collisions(needles: list[dict], decoys_by_needle: dict[str, list[str]]) -> None:
    """A decoy that reuses a real needle's own identifying value makes that needle's
    question unanswerable (or wrongly gradeable from the decoy) — checks every
    needle's required substrings against every decoy in the whole set, not just that
    needle's own decoys.
    """
    if not decoys_by_needle:
        return
    all_decoy_text = "\n".join(t for texts in decoys_by_needle.values() for t in texts).lower()
    for needle in needles:
        for alternatives in needle["required_substrings"]:
            for value in alternatives:
                # Skip short/generic tokens ("0", "17") expected to recur incidentally
                # in unrelated decoy numbers/counts.
                if len(value) < 4:
                    continue
                if value.lower() in all_decoy_text:
                    raise RuntimeError(
                        f"Decoy collision: {needle['id']}'s required value {value!r} appears "
                        "in decoy text — fix the decoy before regenerating."
                    )


def splice(
    source_text: str, needles: list[dict], decoys_by_needle: dict[str, list[str]]
) -> tuple[str, list[dict]]:
    """Insert every needle (and, if any, its decoys) as its own paragraph at a target
    depth; record each placement's actual character offset (paragraph-boundary
    snapped) for questions.json. Decoys are spread across depths distinct from their
    real needle so they aren't trivially adjacent to it.
    """
    paragraphs = re.split(r"\n\s*\n", source_text)
    n = len(paragraphs)

    placements = [{**needle, "needle_id": needle["id"], "is_decoy": False} for needle in needles]
    flat_decoys = [(needle_id, text) for needle_id, texts in decoys_by_needle.items() for text in texts]
    for i, (needle_id, decoy_text) in enumerate(flat_decoys):
        depth = (0.08 + 0.9 * ((i * 7 + 3) % len(flat_decoys)) / len(flat_decoys)) % 0.95
        placements.append({"text": decoy_text, "depth_pct": depth, "needle_id": needle_id, "is_decoy": True})

    # Insert deepest-target-first so earlier insertions don't shift the target
    # indices of shallower placements still to come.
    result_paragraphs = list(paragraphs)
    for placement in sorted(placements, key=lambda p: p["depth_pct"], reverse=True):
        target_index = max(0, min(int(n * placement["depth_pct"]), len(result_paragraphs)))
        result_paragraphs.insert(target_index, placement["text"])

    final_text = "\n\n".join(result_paragraphs)
    for placement in placements:
        placement["char_offset"] = final_text.index(placement["text"])
    placements.sort(key=lambda p: p["char_offset"])
    return final_text, placements


def build_questions(haystack: str, placements: list[dict], with_decoys: bool) -> list[dict]:
    real = [p for p in placements if not p["is_decoy"]]
    decoy_offsets_by_needle: dict[str, list[int]] = {}
    for p in placements:
        if p["is_decoy"]:
            decoy_offsets_by_needle.setdefault(p["needle_id"], []).append(p["char_offset"])

    questions = []
    for p in real:
        question = {
            "id": p["id"],
            "question": p["question"],
            "required_substrings": p["required_substrings"],
            "needle_text": p["text"],
            "char_offset": p["char_offset"],
            "depth_pct_actual": round(p["char_offset"] / len(haystack), 4),
        }
        if with_decoys:
            question["decoy_offsets"] = decoy_offsets_by_needle.get(p["id"], [])
        questions.append(question)
    return questions


def build_tier(tier_name: str) -> None:
    tier = TIERS[tier_name]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    check_no_decoy_collisions(NEEDLES, tier.decoys_by_needle)

    print(f"Fetching {len(tier.sources)} source text(s)...")
    source_text = build_source_text(tier)
    print(f"Source text: {len(source_text):,} chars\n")

    haystack, placements = splice(source_text, NEEDLES, tier.decoys_by_needle)

    haystack_path = DATA_DIR / tier.haystack_file
    haystack_path.write_text(haystack, encoding="utf-8")

    with_decoys = bool(tier.decoys_by_needle)
    questions = build_questions(haystack, placements, with_decoys=with_decoys)
    questions_path = DATA_DIR / tier.questions_file
    questions_path.write_text(json.dumps(questions, indent=2) + "\n", encoding="utf-8")

    total_decoys = sum(len(v) for v in tier.decoys_by_needle.values())
    print(f"Wrote {haystack_path} ({len(haystack):,} chars, {len(tier.sources)} source text(s))")
    print(f"Wrote {questions_path} ({len(questions)} questions, {total_decoys} decoys)")
    for q in questions:
        decoy_note = f", decoys at {q['decoy_offsets']}" if with_decoys else ""
        print(f"  {q['id']}: offset {q['char_offset']:,} ({q['depth_pct_actual']:.1%} through){decoy_note}")


def build_redigest_b() -> None:
    """Build "document B" for the redigest scenario (run_probe.py's
    LARGE_OUTPUT_PROBE_MODE=redigest): a second, topically unrelated large document
    introduced mid-conversation, so the primary sandbox variable holding it is
    genuinely new on the turn that fetches it — unlike re-accessing document A (the
    "easy" tier's haystack.txt, reused as-is here), which the parent typically answers
    from a persisted variable without ever re-triggering digestion (see results.md).
    Fetching document B for the first time forces the gate to fire again, which is
    exactly the condition needed to test whether the digesting subagent's task uses
    this turn's real question or a stale one from earlier in the thread.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    check_no_decoy_collisions(REDIGEST_B_NEEDLES, {})

    print(f"Fetching source text (#{REDIGEST_B_SOURCE.ebook_id} {REDIGEST_B_SOURCE.title!r})...")
    source_text = fetch_book_text(REDIGEST_B_SOURCE.ebook_id)
    print(f"Source text: {len(source_text):,} chars\n")

    haystack, placements = splice(source_text, REDIGEST_B_NEEDLES, {})

    haystack_path = DATA_DIR / "haystack_redigest_b.txt"
    haystack_path.write_text(haystack, encoding="utf-8")

    questions = build_questions(haystack, placements, with_decoys=False)
    questions_path = DATA_DIR / "questions_redigest_b.json"
    questions_path.write_text(json.dumps(questions, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {haystack_path} ({len(haystack):,} chars)")
    print(f"Wrote {questions_path} ({len(questions)} questions)")
    for q in questions:
        print(f"  {q['id']}: offset {q['char_offset']:,} ({q['depth_pct_actual']:.1%} through)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a large_output_probe fixture tier.")
    parser.add_argument(
        "tier",
        nargs="?",
        default="easy",
        choices=sorted(TIERS) + ["redigest_b"],
        help="fixture tier to build (default: easy); 'redigest_b' builds the second "
        "document for LARGE_OUTPUT_PROBE_MODE=redigest (document A reuses 'easy')",
    )
    args = parser.parse_args()
    if args.tier == "redigest_b":
        build_redigest_b()
    else:
        build_tier(args.tier)


if __name__ == "__main__":
    main()
