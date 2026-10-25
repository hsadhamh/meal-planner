"""Chat agent: Claude (tool use) over the RAG knowledge base and the deterministic planner.

Division of labour
  * The LLM talks to the family, extracts the household profile, explains choices and cites sources.
  * Tools do everything factual: retrieval (search_knowledge), maths and composition (generate_plan,
    swap_meal), and files (export_plan). The model is told never to invent numbers or dishes.

Without ANTHROPIC_API_KEY (or the `anthropic` package) the agent falls back to a rule-based loop,
so the POC runs fully offline.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import export
from .knowledge import Knowledge
from .planner import Planner
from .profile import PROTEINS, Household, parse_text

MODEL = os.environ.get("ORE_MODEL", "claude-sonnet-5-5")
OUT = Path(os.environ.get("ORE_OUT", Path(__file__).resolve().parent.parent / "out"))

SYSTEM = """You are Ore Samayal, a friendly South Indian family meal-planning assistant (English or Tamil, match the user).
Rules:
1. Never invent nutrition numbers, dishes or quantities. Use generate_plan / swap_meal for plans and numbers, and
   search_knowledge for guidelines, recipes and baby-food rules. Quote numbers exactly as the tools return them.
2. Cite the Source of every guideline you use, briefly, e.g. "(ICMR-NIN 2020)".
3. Before generating a plan you need: number of adults, children's ages, babies' ages in months, and which of
   egg/chicken/mutton/fish/prawn the family eats. Ask for anything missing in ONE short question; default to
   7 days. Record answers with update_profile.
4. Babies under 12 months: never suggest honey, salt, sugar, juice, tea or cow's milk as a drink; respect choking rules.
5. If someone mentions diabetes, kidney or heart disease, pregnancy or a weight-loss programme, keep planning but say
   portions should be set by their doctor or dietitian. You give meal-planning guidance, not medical advice.
6. After a plan is generated, summarise it in 3-5 lines (diet, protein range, prep overlaps) and give the download link
   returned by export_plan. Do not paste the whole plan."""

TOOLS = [
    {"name": "update_profile", "description": "Set or change household details. Only pass fields you learned.",
     "input_schema": {"type": "object", "properties": {
         "adults": {"type": "integer"}, "kids_ages": {"type": "array", "items": {"type": "integer"}},
         "babies_months": {"type": "array", "items": {"type": "integer"}},
         "allow": {"type": "object", "description": "Which non-veg proteins are eaten",
                   "properties": {p: {"type": "boolean"} for p in PROTEINS}},
         "allergies": {"type": "array", "items": {"type": "string", "enum": ["egg", "peanut", "sesame", "fish", "shellfish", "dairy", "wheat", "soy"]}},
         "conditions": {"type": "array", "items": {"type": "string", "enum": ["diabetes", "kidney", "heart", "pregnancy", "weight_loss"]}},
         "breastfeeding": {"type": "boolean"}, "days": {"type": "integer", "minimum": 1, "maximum": 14},
         "language": {"type": "string", "enum": ["en", "ta"]}}}},
    {"name": "search_knowledge", "description": "Retrieve guideline chunks (ICMR-NIN, WHO, NHS, CDC, FDA) and dish recipes. Returns text with sources.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "kind": {"type": "string", "enum": ["guideline", "dish"]}},
                      "required": ["query"]}},
    {"name": "generate_plan", "description": "Build the meal plan for the current profile. Returns a compact summary with per-day adult nutrition.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "swap_meal", "description": "Regenerate one meal, optionally excluding a protein (e.g. avoid='fish').",
     "input_schema": {"type": "object", "properties": {"day": {"type": "integer"}, "slot": {"type": "string", "enum": ["breakfast", "lunch", "snack", "dinner"]},
                                                       "avoid": {"type": "string"}}, "required": ["day", "slot"]}},
    {"name": "export_plan", "description": "Write the current plan to PDF, CSV and Markdown and return download paths.",
     "input_schema": {"type": "object", "properties": {}}},
]


@dataclass
class Session:
    id: str = "local"
    household: Household = field(default_factory=Household)
    plan: dict | None = None
    messages: list = field(default_factory=list)
    profile_touched: bool = False


class Agent:
    def __init__(self, kb: Knowledge | None = None):
        self.kb = kb or Knowledge()
        self.planner = Planner(self.kb)
        self.client = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic
                self.client = anthropic.Anthropic()
            except ImportError:
                self.client = None

    # ---------------- tools ----------------
    def run_tool(self, s: Session, name: str, args: dict) -> dict:
        h = s.household
        if name == "update_profile":
            for k, v in args.items():
                if k == "allow":
                    h.allow.update(v)
                elif hasattr(h, k):
                    setattr(h, k, v)
            s.profile_touched = True
            return {"ok": True, "profile": h.to_dict()}
        if name == "search_knowledge":
            return {"results": self.kb.search(args["query"], k=4, kind=args.get("kind"))}
        if name == "generate_plan":
            s.plan = self.planner.build_plan(h)
            return self._summary(s.plan)
        if name == "swap_meal":
            if not s.plan:
                return {"error": "No plan yet; call generate_plan first."}
            s.plan = self.planner.swap(h, s.plan, args["day"], args["slot"], args.get("avoid"))
            d = s.plan["days"][args["day"] - 1]
            return {"day": d["day"], "meals": {k: v["adult"] for k, v in d["meals"].items()}, "nutrition": d["nutrition"]}
        if name == "export_plan":
            if not s.plan:
                return {"error": "No plan yet; call generate_plan first."}
            return self.export(s)
        return {"error": f"unknown tool {name}"}

    def export(self, s: Session) -> dict:
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / f"plan_{s.id}"
        export.to_pdf(s.plan, str(stem) + ".pdf")
        Path(str(stem) + ".csv").write_text(export.to_csv(s.plan), encoding="utf-8")
        Path(str(stem) + ".md").write_text(export.to_markdown(s.plan, tamil=s.household.language == "ta"), encoding="utf-8")
        return {"pdf": str(stem) + ".pdf", "csv": str(stem) + ".csv", "markdown": str(stem) + ".md",
                "download_url": f"/plan/{s.id}.pdf"}

    @staticmethod
    def _summary(plan: dict) -> dict:
        return {"household": plan["household"], "metrics": plan["metrics"], "safety_notes": plan["safety_notes"],
                "days": [{"day": d["day"], "breakfast": d["meals"]["breakfast"]["adult"], "lunch": d["meals"]["lunch"]["adult"],
                          "dinner": d["meals"]["dinner"]["adult"], "adult_protein_g": d["nutrition"]["protein_g"],
                          "adult_carb_pct": d["nutrition"]["carb_pct"], "prep_tonight": d["prep_tonight"]} for d in plan["days"]]}

    # ---------------- chat ----------------
    def chat(self, s: Session, text: str) -> str:
        return self._chat_llm(s, text) if self.client else self._chat_offline(s, text)

    def _chat_llm(self, s: Session, text: str) -> str:
        s.messages.append({"role": "user", "content": text})
        for _ in range(8):
            resp = self.client.messages.create(model=MODEL, max_tokens=1500, system=SYSTEM, tools=TOOLS, messages=s.messages)
            s.messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out = self.run_tool(s, b.name, b.input)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, ensure_ascii=False)})
            s.messages.append({"role": "user", "content": results})
        return "Sorry, I could not finish that request. Please try again."

    # Offline fallback: rules + retrieval, no LLM.
    PLAN_WORDS = re.compile(r"\b(plan|menu|week|days?|generate|make|create|pdf|download)\b|திட்ட|மெனு")
    QUESTION = re.compile(r"\?|\b(can|should|is|are|how|what|why|when|which)\b|சாப்பிடலாமா|எப்படி|ஏன்")

    def _chat_offline(self, s: Session, text: str) -> str:
        before = json.dumps(s.household.to_dict(), sort_keys=True)
        s.household = parse_text(text, s.household)
        changed = json.dumps(s.household.to_dict(), sort_keys=True) != before
        s.profile_touched |= changed
        t = text.lower()
        if m := re.search(r"swap\s+day\s*(\d+)\s+(breakfast|lunch|snack|dinner)(?:.*?no\s+(\w+))?", t):
            if s.plan:
                r = self.run_tool(s, "swap_meal", {"day": int(m.group(1)), "slot": m.group(2), "avoid": m.group(3)})
                files = self.export(s)
                return f"Day {r['day']} updated: {r['meals'][m.group(2)]}. New PDF: {files['pdf']}"
        if self.QUESTION.search(t) and not self.PLAN_WORDS.search(t):
            return self._answer(text)
        if self.PLAN_WORDS.search(t) or changed:
            if not s.profile_touched:
                return ("Tell me about the family: how many adults, children's ages, any baby's age in months, "
                        "and which of egg / chicken / mutton / fish / prawn you eat.")
            s.plan = self.planner.build_plan(s.household)
            files = self.export(s)
            m = s.plan["metrics"]
            return (f"Here is a {len(s.plan['days'])}-day {s.household.diet_label()} plan for {s.household.adults} adults"
                    + (f", children {s.household.kids_ages}" if s.household.kids_ages else "")
                    + (f", baby {s.household.babies_months} months" if s.household.babies_months else "")
                    + f".\nAdult protein {m['adult_protein_g'][0]}-{m['adult_protein_g'][1]} g/day; carbs {m['adult_carb_pct'][0]}-{m['adult_carb_pct'][1]}% of energy."
                    + "\nOne batter covers 2-3 breakfasts; each pulse pot feeds a snack and the next lunch"
                    + ("; lunch meat becomes the dinner pepper fry" if any(s.household.allow.get(p) for p in ("chicken", "mutton")) else "")
                    + ("; the baby's portion is lifted before salt." if s.household.babies_months else ".")
                    + ("\nCheck: " + " ".join(s.plan["warnings"]) if s.plan.get("warnings") else "")
                    + f"\nDownload: {files['pdf']} (also CSV and Markdown).")
        return self._answer(text)

    def _answer(self, text: str) -> str:
        hits = self.kb.search(text, k=2)
        if not hits:
            return "I could not find that in my knowledge base. Try asking about baby foods, protein, carbs, fibre or a dish."
        parts = []
        for hgt in hits:
            src = f" (Source: {hgt['source']})" if hgt["source"].startswith("http") or hgt["kind"] == "guideline" else ""
            parts.append(f"• {hgt['title']}: {hgt['text'][:420]}{src}")
        return "From the knowledge base:\n" + "\n".join(parts)
