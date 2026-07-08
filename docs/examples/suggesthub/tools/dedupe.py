from __future__ import annotations

import math
import re
from collections import Counter

from docs.examples.suggesthub.app import repository

TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.lower()))


def _tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.lower())


def lexical_similarity(left: str, right: str) -> float:
    left_counts = Counter(_tokens(left))
    right_counts = Counter(_tokens(right))
    if not left_counts or not right_counts:
        return 0.0
    common = set(left_counts) & set(right_counts)
    dot = sum(left_counts[token] * right_counts[token] for token in common)
    left_norm = math.sqrt(sum(count * count for count in left_counts.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counts.values()))
    cosine = dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
    jaccard = len(common) / len(set(left_counts) | set(right_counts))
    return round((0.72 * cosine) + (0.28 * jaccard), 4)


def _sentence_transformer_similarity(query: str, candidates: list[str]) -> list[float] | None:
    try:
        from fastembed import TextEmbedding
    except Exception:
        return None

    try:
        model = TextEmbedding()
        vectors = list(model.embed([query, *candidates]))
    except Exception:
        return None

    if len(vectors) != len(candidates) + 1:
        return None
    query_vec = vectors[0]
    scores = []
    for vector in vectors[1:]:
        dot = sum(float(a) * float(b) for a, b in zip(query_vec, vector))
        query_norm = math.sqrt(sum(float(a) * float(a) for a in query_vec))
        vector_norm = math.sqrt(sum(float(b) * float(b) for b in vector))
        scores.append(round(dot / (query_norm * vector_norm), 4) if query_norm and vector_norm else 0.0)
    return scores


def find_similar(conn, query: str, limit: int = 3, threshold: float = 0.22) -> list[dict]:
    suggestions = repository.list_suggestions(conn, sort="votes")
    candidate_texts = [
        f"{item['title']} {item['description']} {item['category']} {item['location']} {item['impact']}"
        for item in suggestions
    ]
    semantic_scores = _sentence_transformer_similarity(query, candidate_texts)
    matches = []
    for index, item in enumerate(suggestions):
        lexical = lexical_similarity(query, candidate_texts[index])
        semantic = semantic_scores[index] if semantic_scores else None
        score = max(lexical, semantic or 0.0)
        if score >= threshold:
            match = dict(item)
            match["similarity"] = score
            match["similarity_source"] = "embedding" if semantic and semantic >= lexical else "lexical"
            matches.append(match)
    return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:limit]


def infer_category(text: str) -> str:
    value = normalize_text(text)
    rules = [
        ("Facilities", ["desk", "chair", "floor", "room", "hvac", "parking", "building", "elevator"]),
        ("IT", ["wifi", "laptop", "vpn", "slack", "network", "software", "printer", "badge access"]),
        ("Food & Beverage", ["cafeteria", "coffee", "breakfast", "lunch", "snack", "food"]),
        ("Safety", ["security", "badge", "safety", "hazard", "access", "emergency"]),
        ("Wellness", ["health", "wellness", "ergonomic", "stress", "fitness"]),
        ("Culture", ["meeting", "recognition", "team", "communication", "culture"]),
    ]
    for category, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return category
    return "Other"


def infer_location(text: str) -> str:
    value = text.lower()
    for location in ["SVL Floor 3", "SVL", "Austin", "NYC", "Raleigh", "Poughkeepsie"]:
        if location.lower() in value:
            return location
    floor_match = re.search(r"floor\s+(\d+)", value)
    if floor_match:
        return f"Floor {floor_match.group(1)}"
    return "Unspecified"


def build_draft_from_text(text: str, thread_id: str = "suggesthub-demo") -> dict:
    category = infer_category(text)
    location = infer_location(text)
    clean = text.strip().rstrip(".")
    title_words = clean.split()[:10]
    title = " ".join(title_words)
    if title:
        title = title[0].upper() + title[1:]
    return {
        "thread_id": thread_id,
        "raw_text": text,
        "title": title or "Workplace improvement suggestion",
        "description": clean or "Employee reported a workplace improvement opportunity.",
        "category": category,
        "location": location,
        "impact": "Employee productivity, trust, or workplace experience is affected.",
    }
