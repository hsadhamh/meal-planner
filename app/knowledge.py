"""Knowledge base loading and retrieval (RAG layer).

Two corpora are indexed with a small BM25 implementation (no external deps):
  * guideline chunks from kb/guidelines.md, each with a Source line
  * dish documents built from kb/dishes.json

Swap `BM25Index` for an embedding store (pgvector, Chroma, Azure AI Search) in production;
the `search()` contract stays the same.
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

KB = Path(__file__).parent / "kb"
_TOKEN = re.compile(r"[a-z0-9]+|[஀-௿]+")
_STOP = set("a an the and or of to for in on at is are be with by from as it this that per day a".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class Doc:
    id: str
    title: str
    text: str
    source: str
    kind: str  # "guideline" | "dish"
    meta: dict = field(default_factory=dict)


class BM25Index:
    def __init__(self, docs: list[Doc], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.tokens = [tokenize(d.title + " " + d.title + " " + d.text) for d in docs]
        self.avg = sum(map(len, self.tokens)) / max(1, len(self.tokens))
        df: dict[str, int] = {}
        for toks in self.tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 4, kind: str | None = None) -> list[tuple[float, Doc]]:
        q = tokenize(query)
        scored = []
        for doc, toks in zip(self.docs, self.tokens):
            if kind and doc.kind != kind:
                continue
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(toks) / self.avg))
            if s > 0:
                scored.append((s, doc))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


def load_foods() -> dict[str, dict]:
    with open(KB / "foods.csv", newline="", encoding="utf-8") as f:
        return {r["id"]: {**r, **{k: float(r[k]) for k in ("kcal", "protein_g", "fat_g", "carb_g", "fibre_g")}} for r in csv.DictReader(f)}


def load_dishes() -> dict[str, dict]:
    data = json.loads((KB / "dishes.json").read_text(encoding="utf-8"))
    return {d["id"]: d for d in data["dishes"]}


def load_guidelines() -> list[Doc]:
    text = (KB / "guidelines.md").read_text(encoding="utf-8")
    docs = []
    for i, block in enumerate(re.split(r"\n## ", text)[1:]):
        title, _, body = block.partition("\n")
        src = re.search(r"^Source:\s*(.+)$", body, re.M)
        body = re.sub(r"^Source:.*$", "", body, flags=re.M).strip()
        docs.append(Doc(id=f"g{i}", title=title.strip(), text=body, source=src.group(1).strip() if src else "", kind="guideline"))
    return docs


class Knowledge:
    """Single entry point for the planner and the agent."""

    def __init__(self):
        self.foods = load_foods()
        self.dishes = load_dishes()
        dish_docs = [
            Doc(id=d["id"], title=f'{d["name"]} ({d["ta"]})',
                text=f'{d["role"]} {d["protein"]} ingredients: {", ".join(d["ingredients"])}. '
                     f'Allergens: {", ".join(d["allergens"]) or "none"}. Baby: {d.get("baby") or "not suitable"}. '
                     + " ".join(d.get("steps", [])),
                source="kb/dishes.json", kind="dish", meta=d)
            for d in self.dishes.values()
        ]
        self.guidelines = load_guidelines()
        self.index = BM25Index(self.guidelines + dish_docs)

    def search(self, query: str, k: int = 4, kind: str | None = None) -> list[dict]:
        return [
            {"id": d.id, "title": d.title, "text": d.text, "source": d.source, "kind": d.kind, "score": round(s, 2)}
            for s, d in self.index.search(query, k=k, kind=kind)
        ]

    def nutrients(self, ingredients: dict[str, float]) -> dict[str, float]:
        tot = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "fibre_g": 0.0}
        for food, grams in ingredients.items():
            f = self.foods.get(food)
            if not f:
                continue
            for k in tot:
                tot[k] += f[k] * grams / 100
        return tot
