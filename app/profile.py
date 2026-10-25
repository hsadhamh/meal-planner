"""Household profile, targets, and a rule-based parser (used when no LLM key is set)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

PROTEINS = ["egg", "chicken", "mutton", "fish", "prawn"]
CONDITIONS = ["diabetes", "kidney", "heart", "pregnancy", "weight_loss"]


@dataclass
class Household:
    adults: int = 2
    kids_ages: list[int] = field(default_factory=list)      # 3-12 years
    babies_months: list[int] = field(default_factory=list)  # 6-12 months
    allow: dict[str, bool] = field(default_factory=lambda: {p: False for p in PROTEINS})
    allergies: list[str] = field(default_factory=list)      # egg, peanut, sesame, fish, shellfish, dairy, wheat, soy
    conditions: list[str] = field(default_factory=list)
    breastfeeding: bool = False
    days: int = 7
    language: str = "en"

    def diet_label(self) -> str:
        on = [p for p in PROTEINS if self.allow.get(p)]
        if not on:
            return "vegetarian"
        if on == ["egg"]:
            return "eggetarian"
        return "non-vegetarian (" + ", ".join(on) + ")"

    def portion_factor(self) -> float:
        """Cooking quantity in adult-equivalents."""
        return self.adults + 0.6 * len(self.kids_ages) + 0.15 * len(self.babies_months)

    def targets(self) -> dict:
        t = {
            "adult_protein_g": 75 if "kidney" not in self.conditions else 54,
            "adult_carb_pct_max": 45,
            "adult_fibre_g": 35,
            "oil_tsp_per_adult": 4,
        }
        return t

    def to_dict(self) -> dict:
        d = asdict(self)
        d["diet"] = self.diet_label()
        d["portion_factor"] = round(self.portion_factor(), 2)
        return d


WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else WORDNUM.get(s, 0)


def parse_text(text: str, base: Household | None = None) -> Household:
    """Best-effort extraction of household details from free text (English + a few Tamil words)."""
    h = base or Household()
    t = text.lower()
    n = r"(\d+|one|two|three|four|five|six|seven|eight)"
    if m := re.search(n + r"\s+(adults?|elders?|grown[- ]ups?|பெரியவர்)", t):
        h.adults = max(1, _num(m.group(1)))
    kids = [int(a) for a in re.findall(r"(\d{1,2})\s*(?:-|\s)?(?:year|yr|yrs|வயது)", t) if 1 < int(a) < 13]
    if kids:
        h.kids_ages = kids
    babies = [int(a) for a in re.findall(r"(\d{1,2})\s*(?:-|\s)?(?:month|mo\b|மாத)", t) if 5 < int(a) < 13]
    if babies:
        h.babies_months = babies
    veg_words = ["pure veg", "vegetarian", "veg only", "no meat", "சைவம்"]
    if any(w in t for w in ["non-veg", "non veg", "nonveg", "அசைவம்"]):
        h.allow.update({"egg": True, "chicken": True, "mutton": True, "fish": True})
    elif "egg" in t and ("eggetarian" in t or "only egg" in t or "veg + egg" in t or "veg and egg" in t):
        h.allow = {p: p == "egg" for p in PROTEINS}
    elif any(w in t for w in veg_words):
        h.allow = {p: False for p in PROTEINS}
    for p, words in {"chicken": ["chicken", "கோழி"], "mutton": ["mutton", "goat", "ஆட்டு"], "fish": ["fish", "மீன்"],
                     "prawn": ["prawn", "shrimp", "இறால்"], "egg": ["egg", "முட்டை"]}.items():
        for w in words:
            if re.search(r"(no|without|don't eat|dont eat|avoid)\s+" + w, t):
                h.allow[p] = False
            elif w in t and (p == "egg" or not any(v in t for v in veg_words)):
                h.allow[p] = True
    for a in ["peanut", "sesame", "shellfish", "dairy", "wheat", "soy"]:
        if re.search(a + r"\s+allerg|allerg\w*\s+(to\s+)?" + a, t):
            h.allergies.append(a)
    for c, words in {"diabetes": ["diabet", "sugar patient"], "kidney": ["kidney", "ckd"], "heart": ["heart", "cholesterol"],
                     "pregnancy": ["pregnan"], "weight_loss": ["weight loss", "lose weight"]}.items():
        if any(w in t for w in words) and c not in h.conditions:
            h.conditions.append(c)
    if "breastfeed" in t or "nursing" in t:
        h.breastfeeding = True
    if m := re.search(r"(\d+)\s*(?:day|days|நாள்)", t):
        h.days = min(14, max(1, int(m.group(1))))
    elif m := re.search(r"(\d|one|two)\s*(?:weeks?|வார)", t):
        h.days = min(14, 7 * max(1, _num(m.group(1))))
    elif "fortnight" in t:
        h.days = 14
    elif "week" in t or "வாரம்" in t:
        h.days = 7
    return h
