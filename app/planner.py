"""Deterministic meal planner.

The LLM never invents meals or numbers. It calls `build_plan()`, which:
  1. picks dishes from the knowledge base that fit the household's diet and allergies,
  2. follows South Indian prep chains (one batter for 2-3 breakfasts, lunch meat -> dinner fry),
  3. computes adult nutrition from ingredient grams and auto-adjusts to hit targets,
  4. writes kid and baby versions of every meal and a scaled grocery list.
"""
from __future__ import annotations

import math
from itertools import cycle

from .knowledge import Knowledge
from .profile import Household

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MILLETS = ["samai", "kambu", "varagu", "kuthiraivali"]
# (tiffin kind, batter number) — batter numbers group consecutive breakfasts on one batter
BREAKFAST_PATTERN = [("idli", 0), ("dosa", 0), ("toast", None), ("dosa", 1), ("idli", 1), ("uthappam", 1), ("pesarattu", None),
                     ("idli", 2), ("uthappam", 2), ("toast", None), ("adai", None), ("paniyaram", None), ("ragi_dosa", None), ("toast", None)]
SIDE_FOR = {"idli": "sambar", "dosa": "peanut_chutney", "uthappam": "onion_chutney", "adai": "pottukadalai_chutney",
            "paniyaram": "peanut_chutney", "pesarattu": "pottukadalai_chutney", "ragi_dosa": "peanut_chutney", "toast": None}
LUNCH_SLOTS = ["meatA", "legume", "fish", "legume", "egg", "fish2", "meatB"]
SLOT_ORDER = {
    "meatA": ["chicken", "mutton", "fish", "prawn", "egg", "veg"],
    "meatB": ["mutton", "chicken", "prawn", "fish", "egg", "veg"],
    "fish": ["fish", "prawn", "chicken", "egg", "veg"],
    "fish2": ["prawn", "fish", "chicken", "egg", "veg"],
    "egg": ["egg", "chicken", "veg"],
}
BY_TYPE = {
    "chicken": ["chicken_kuzhambu"], "mutton": ["mutton_kuzhambu"], "prawn": ["prawn_thokku"],
    "fish": ["fish_kuzhambu", "fish_fry", "sardine_kuzhambu"], "egg": ["egg_kuzhambu"], "veg": ["paneer_kurma", "soya_kuzhambu"],
}
LEGUMES = ["keerai_paruppu", "rajma_kuzhambu", "karamani_kuzhambu", "kollu_kuzhambu", "kadala_curry", "sambar_lunch"]
SOAK = {"rajma_kuzhambu": "rajma", "karamani_kuzhambu": "karamani", "kollu_kuzhambu": "kollu (horse gram)", "kadala_curry": "black chana"}
GRAINS = ["grain_redrice", "grain_rice", "grain_brownrice", "grain_samai"]
VEG_SIDES = ["beans_carrot_poriyal", "cabbage_peas_poriyal", "snakegourd_kootu", "beetroot_poriyal", "okra_poriyal",
             "beans_usili", "cauliflower_poriyal", "keerai_poriyal", "avial"]
SNACKS = ["sundal_chana", "sprouts_salad", "chana_peanuts", "boiled_groundnut", "sundal_karamani", "sprouts_salad", "sundal_kollu"]
DINNERS = ["paneer_bhurji", "dal_tadka", "egg_bhurji", "chana_masala", "pongal_dalveg", "soya_mushroom_fry", "moong_chilla"]
LOWCARB_DINNERS = ["paneer_bhurji", "egg_bhurji", "soya_mushroom_fry", "moong_chilla"]
FRUITS = [("Apple", "apple", 150), ("Guava", "guava", 150), ("Watermelon", "watermelon", 250), ("Grapes", "grapes", 100),
          ("Dragon fruit", "dragonfruit", 150), ("Pomegranate", "pomegranate", 100), ("Papaya", "papaya", 200)]
DRINKS = ["Tender coconut water", "Buttermilk (moru)", "Cucumber-mint water"]
WATERS = ["Jeera water", "Sombu water", "Vettiver water", "Lemon water", "Athimathuram water", "Ginger-cinnamon water", "Thulasi water"]
BUY = {"chicken": 1 / 0.75, "mutton": 1 / 0.75, "mackerel": 1 / 0.6, "pomfret": 1 / 0.6, "sardine": 1 / 0.55, "prawn": 1 / 0.55}
LOAF = {"atta": 325, "oats": 50, "ragi": 50, "jowar": 25, "roastedgram": 50, "flax": 15, "sesame": 15, "pumpkinseed": 25, "oil": 30}


class Planner:
    def __init__(self, kb: Knowledge):
        self.kb = kb
        d = kb.dishes
        # lunch sambar = 1.5 katori of the breakfast sambar recipe
        if "sambar_lunch" not in d:
            s = dict(d["sambar"]); s.update(id="sambar_lunch", name="Mixed-vegetable sambar (1½ katori)", role="main",
                                            ingredients={"toor": 35, "veg": 150, "oil": 3})
            d["sambar_lunch"] = s

    # ---------- filters ----------
    def ok(self, h: Household, dish_id: str) -> bool:
        d = self.kb.dishes[dish_id]
        if d["protein"] != "veg" and not h.allow.get(d["protein"]):
            return False
        return not set(d["allergens"]) & set(h.allergies)

    def pick(self, h: Household, ids: list[str], used: list[str], avoid_last: int = 2) -> str | None:
        ok = [i for i in ids if self.ok(h, i)]
        if not ok:
            return None
        recent = used[-avoid_last:] if avoid_last else []
        fresh = [i for i in ok if i not in recent]
        choice = sorted(fresh or ok, key=lambda i: used.count(i))[0]
        used.append(choice)
        return choice

    # ---------- plan ----------
    def build_plan(self, h: Household) -> dict:
        kb, t = self.kb, h.targets()
        if "egg" in h.allergies:
            h.allow["egg"] = False
        if "fish" in h.allergies:
            h.allow["fish"] = False
        if "shellfish" in h.allergies:
            h.allow["prawn"] = False
        used: dict[str, list[str]] = {k: [] for k in ("main", "legume", "grain", "veg", "snack", "dinner", "bprot", "fish", "vegmain")}
        days = []
        for i in range(h.days):
            days.append(self._day(h, i, used, t))
        for i, day in enumerate(days):
            day["prep_tonight"] = self._prep_notes(days, i)
        prot = [d["nutrition"]["protein_g"] for d in days]
        carb = [d["nutrition"]["carb_pct"] for d in days]
        return {
            "household": h.to_dict(), "targets": t, "days": days,
            "metrics": {"adult_protein_g": [min(prot), max(prot)], "adult_carb_pct": [min(carb), max(carb)],
                        "days_meeting_protein": sum(p >= t["adult_protein_g"] for p in prot)},
            "warnings": self._warnings(h, days, t),
            "grocery": self.grocery(h, days),
            "safety_notes": self.safety_notes(h),
            "sources": sorted({g.source for g in kb.guidelines if g.source.startswith("http")}),
        }

    def _breakfast(self, h, i, used):
        kind, batter = BREAKFAST_PATTERN[i % 14]
        if kind in ("idli", "dosa", "uthappam"):
            millet = MILLETS[(batter + (i // 14)) % len(MILLETS)]
            tiffin = f"{kind}_{millet}"
        else:
            tiffin = {"toast": "mg_toast"}.get(kind, kind)
        if not self.ok(h, tiffin):  # e.g. wheat or sesame allergy blocks toast
            tiffin = f"dosa_{MILLETS[i % len(MILLETS)]}"
        items = [tiffin]
        side = SIDE_FOR.get(kind)
        if side and self.ok(h, side):
            items.append(side)
        elif side:
            items.append("pottukadalai_chutney")
        egg_day = h.allow.get("egg") and i % 7 not in (2, 5)  # 5 egg breakfasts a week, 2 paneer/curd
        prot = self.pick(h, ["boiled_eggs", "egg_podimas", "veg_omelette"], used["bprot"], 1) if egg_day else None
        if not prot:
            prot = self.pick(h, ["paneer_tawa", "curd_cup"], used["bprot"], 1) or "sprouts_salad"
        items.append(prot)
        return items

    def _lunch_main(self, h, i, used):
        slot = LUNCH_SLOTS[i % 7]
        if slot == "legume":
            return self.pick(h, LEGUMES, used["legume"], 4)
        types = used.setdefault("types", [])
        for typ in SLOT_ORDER[slot]:
            if typ == "veg":
                # rotate paneer / soya with a second legume so no single food dominates the week
                pick = self.pick(h, BY_TYPE["veg"] + ["kadala_curry", "karamani_kuzhambu"], used["vegmain"], 2)
                types.append("veg")
                return pick
            if not h.allow.get(typ) or types[-6:].count(typ) >= 2:
                continue  # each meat, fish or egg at most twice in a week of lunches
            pick = self.pick(h, BY_TYPE[typ], used["fish"] if typ == "fish" else used["main"], 1)
            if pick:
                types.append(typ)
                return pick
        types.append("legume")
        return self.pick(h, LEGUMES, used["legume"], 4)

    def _day(self, h, i, used, t):
        kb = self.kb
        B = self._breakfast(h, i, used)
        main = self._lunch_main(h, i, used)
        L = [self.pick(h, GRAINS, used["grain"], 1), main, self.pick(h, VEG_SIDES, used["veg"], 3), "salad"]
        if "dairy" not in h.allergies:
            L.append("curd_bowl")
        if kb.dishes[main]["protein"] in ("fish", "prawn") or main in ("keerai_paruppu",):
            L.append("rasam")
        S = [self.pick(h, SNACKS, used["snack"], 2) or "sprouts_salad"]
        chain = kb.dishes[main].get("chain")
        if chain and self.ok(h, chain):
            D = ["dgrain_none" if i % 2 else "chapathi", chain, "salad"]
        else:
            legume_lunch = main in LEGUMES or main in ("kadala_curry", "karamani_kuzhambu")
            pool = LOWCARB_DINNERS if legume_lunch else DINNERS  # pulses carry carbs; balance with a lighter dinner
            dm = self.pick(h, pool, used["dinner"], 3) or self.pick(h, DINNERS, used["dinner"], 3) or "dal_tadka"
            D = ["dgrain_none" if dm in ("pongal_dalveg", "moong_chilla") else "chapathi", dm, "salad"]
        if not self.ok(h, D[0]):  # wheat allergy: no chapathi
            D[0] = "dgrain_none"
        meals = {"breakfast": B, "lunch": L, "snack": S, "dinner": D}
        fruit = FRUITS[i % len(FRUITS)]
        drink = DRINKS[i % len(DRINKS)]
        if "dairy" in h.allergies and drink.startswith("Buttermilk"):
            drink = "Lemon-mint water"
        extras = {"almond": 12, "walnut": 8, fruit[1]: fruit[2]}
        if "dairy" not in h.allergies:
            extras["milk"] = 100
        if drink.startswith("Buttermilk"):
            extras["curd"] = 70
        if drink.startswith("Tender"):
            extras["coconutwater"] = 250
        adjustments = []
        # Auto-adjust loop: if carbs are over the cap take the next carb step; else if protein is short add the next
        # low-carb protein at 4:30. Every change is recorded so the family (and the agent) can see why.
        cap, st = t["adult_carb_pct_max"], {"scale": 1.0, "bscale": 1.0}

        def lunch(x, msg):
            st["scale"] = x
            return msg

        def nochapathi():
            if D[0] != "chapathi":
                return None
            D[0] = "dgrain_none"
            return "Dinner chapathi dropped for adults (children keep theirs)"

        def tiffin():
            st["bscale"] = 0.7
            return "Breakfast tiffin cut by a third for adults (one idli fewer or a thinner dosa)"

        carb_steps = [lambda: lunch(0.75, "Lunch grain trimmed to ¾ katori"), nochapathi,
                      lambda: lunch(0.5, "Lunch grain cut to ½ katori; take more poriyal"), tiffin]
        boosters = [b for b in ("curd_cup", "paneer_tawa", "boiled_eggs", "soya_sundal", "sprouts_salad") if self.ok(h, b) and b not in S]
        nut = self._nutrition(meals, extras)
        for _ in range(10):
            if nut["carb_pct"] > cap and carb_steps:
                msg = carb_steps.pop(0)()
                if msg:
                    adjustments.append(msg + " to keep carbs under the cap")
            elif nut["protein_g"] < t["adult_protein_g"] and boosters and len(meals["snack"]) < 3:
                b = boosters.pop(0)
                meals["snack"].append(b)
                adjustments.append(f"Added {kb.dishes[b]['name']} at 4:30 to reach the protein target")
            else:
                break
            nut = self._nutrition(meals, extras, grain_scale=st["scale"], bscale=st["bscale"])
        scale, bscale = st["scale"], st["bscale"]
        return {
            "day": i + 1, "weekday": WEEKDAYS[i % 7], "morning_water": WATERS[i % 7],
            "fruit": fruit[0], "drink": drink,
            "meals": {slot: self._describe(h, slot, items) for slot, items in meals.items()},
            "item_ids": meals, "grain_scale": scale, "tiffin_scale": bscale,
            "nutrition": nut, "adjustments": adjustments,
        }

    @staticmethod
    def _warnings(h, days, t):
        low = [d["day"] for d in days if d["nutrition"]["protein_g"] < t["adult_protein_g"]]
        high = [d["day"] for d in days if d["nutrition"]["carb_pct"] > t["adult_carb_pct_max"]]
        w = []
        if low:
            w.append(f"Adult protein is below {t['adult_protein_g']} g on day(s) {', '.join(map(str, low))}"
                     + (" - with no dairy, eggs or meat, consider tofu or a dietitian-approved protein source" if not any(h.allow.values()) else ""))
        if high:
            w.append(f"Adult carbs are above {t['adult_carb_pct_max']}% on day(s) {', '.join(map(str, high))}; take extra poriyal and less grain")
        return w

    def _nutrition(self, meals, extras, grain_scale=1.0, bscale=1.0):
        ing: dict[str, float] = dict(extras)
        for slot, items in meals.items():
            for did in items:
                d = self.kb.dishes[did]
                f = grain_scale if (slot == "lunch" and d["role"] == "grain") else bscale if d["role"] == "tiffin" else 1.0
                for k, g in d["ingredients"].items():
                    ing[k] = ing.get(k, 0) + g * f
        n = self.kb.nutrients(ing)
        return {"kcal": round(n["kcal"]), "protein_g": round(n["protein_g"]), "carb_g": round(n["carb_g"]),
                "fat_g": round(n["fat_g"]), "fibre_g": round(n["fibre_g"]),
                "carb_pct": round(n["carb_g"] * 4 / max(1, n["kcal"]) * 100)}

    def _describe(self, h, slot, items):
        ds = [self.kb.dishes[i] for i in items]
        adult = " + ".join(d["name"] for d in ds if d["id"] != "dgrain_none")
        out = {"adult": adult, "tamil": " + ".join(d["ta"] for d in ds if d["id"] != "dgrain_none")}
        if h.kids_ages:
            notes = [f'{d["name"].split(" (")[0]}: {d["kid"]}' for d in ds if d.get("kid")]
            out["kids"] = "Same dishes, less chilli, full grain portion" + (" (" + "; ".join(notes) + ")" if notes else "")
        if h.babies_months:
            main = next((d for d in ds if d["role"] in ("main", "dmain", "tiffin", "snack", "bprotein") and d.get("baby")), None)
            out["baby"] = (main["baby"] if main else "Soft dal-rice mash with a few drops of ghee") + "; no salt, sugar or honey"
        return out

    def _prep_notes(self, days, i):
        notes = []
        if i + 1 >= len(days):
            return notes
        nxt = days[i + 1]["item_ids"]
        kind, batter = BREAKFAST_PATTERN[(i + 1) % 14]
        prev_batter = BREAKFAST_PATTERN[i % 14][1]
        if batter is not None and batter != prev_batter:
            millet = MILLETS[(batter + ((i + 1) // 14)) % len(MILLETS)]
            notes.append(f"Grind {millet} idli/dosa batter tonight (3 millet : 1 urad); set the baby's bowl aside before salt")
        if kind in ("adai", "pesarattu"):
            notes.append("Soak the adai / pesarattu dal mix tonight")
        main = nxt["lunch"][1]
        if main in SOAK:
            notes.append(f"Soak {SOAK[main]} tonight; cook half extra for the snack sundal")
        prot = self.kb.dishes[main]["protein"]
        if prot in ("chicken", "mutton", "fish", "prawn"):
            notes.append(f"Move tomorrow's {prot} from freezer to fridge")
        notes.append("Set curd for tomorrow")
        return notes

    # ---------- grocery ----------
    def grocery(self, h: Household, days: list[dict]) -> list[dict]:
        weeks = []
        P = h.portion_factor()
        eaters = h.adults + len(h.kids_ages)
        for w in range(math.ceil(len(days) / 7)):
            acc: dict[str, float] = {}
            fruit: dict[str, int] = {}
            for day in days[w * 7:(w + 1) * 7]:
                for slot, items in day["item_ids"].items():
                    for did in items:
                        d = self.kb.dishes[did]
                        f = day["grain_scale"] if (slot == "lunch" and d["role"] == "grain") else 1.0
                        for k, g in d["ingredients"].items():
                            acc[k] = acc.get(k, 0) + g * f * P
                if "dairy" not in h.allergies:
                    acc["milk"] = acc.get("milk", 0) + 100 * h.adults + 200 * len(h.kids_ages)
                acc["almond"] = acc.get("almond", 0) + 12 * h.adults + 5 * len(h.kids_ages)
                acc["walnut"] = acc.get("walnut", 0) + 8 * h.adults
                for k in ("onion", "tomato"):
                    acc[k] = acc.get(k, 0) + 50 * P
                if day["drink"].startswith("Buttermilk"):
                    acc["curd"] = acc.get("curd", 0) + 70 * eaters
                fruit[day["fruit"]] = fruit.get(day["fruit"], 0) + eaters
            loaves = 0
            if acc.get("bread"):
                loaves = max(1, math.ceil(acc.pop("bread") / 53 / 16))
                for k, g in LOAF.items():
                    acc[k] = acc.get(k, 0) + g * loaves
            milk = acc.get("milk", 0) + acc.pop("curd", 0) + acc.pop("hungcurd", 0) * 2.2
            if milk:
                acc["milk"] = milk
            items = []
            for k, v in sorted(acc.items(), key=lambda kv: (self.kb.foods.get(kv[0], {}).get("category", "z"), kv[0])):
                if v < 1 or k not in self.kb.foods:
                    continue
                v *= BUY.get(k, 1)
                name = self.kb.foods[k]["name"].replace(", raw edible", "").replace(", edible", "")
                if k == "egg":
                    q = f"{math.ceil(v / 50)} eggs"
                elif k == "milk":
                    q = f"{math.ceil(v / 500) / 2:g} L (includes setting curd)"
                elif k == "oil":
                    q = f"{math.ceil(v / 50) * 50} ml"
                elif k == "coconut":
                    q = f"{math.ceil(v / 250)} coconuts"
                elif k == "greens":
                    q = f"{math.ceil(v / 250)} bunches"
                elif v >= 1000:
                    q = f"{math.ceil(v / 250) / 4:g} kg"
                else:
                    q = f"{math.ceil(v / 50) * 50} g"
                if k in BUY:
                    name += " (as bought)"
                items.append({"item": name, "qty": q, "category": self.kb.foods[k]["category"]})
            for f, n in fruit.items():
                items.append({"item": f, "qty": f"{n} portions", "category": "fruit"})
            if loaves:
                items.append({"item": "Instant yeast", "qty": f"{loaves} sachet{'s' if loaves > 1 else ''} (makes {loaves} multigrain {'loaves' if loaves > 1 else 'loaf'})", "category": "pantry"})
            if h.babies_months:
                items.append({"item": "Sprouted ragi flour (baby porridge)", "qty": "250 g", "category": "pantry"})
            weeks.append({"week": w + 1, "items": items})
        return weeks

    def safety_notes(self, h: Household) -> list[str]:
        notes = ["Estimates use approximate food-composition values; real meals vary by 10-15%."]
        if h.babies_months:
            notes += ["Baby: breast milk or formula stays the main food; 2-3 meals a day at 6-8 months, 3-4 at 9-11 months (WHO).",
                      "Baby: no honey, salt, sugar, juice, tea or cow's milk as a drink before 12 months (NHS, CDC).",
                      "Baby: grind nuts and seeds; mash legumes; remove every fish bone; skip sardines and prawns."]
        if h.kids_ages:
            notes.append("Children keep full grain portions; carb reduction applies to adults only.")
        if h.breastfeeding:
            notes.append("Breastfeeding mother: normal grain portion plus one extra protein serving and snack (ICMR +520 kcal, +13 g protein).")
        if h.conditions:
            notes.append("Medical conditions noted (" + ", ".join(h.conditions) + "): ask a doctor or dietitian to set grain, oil and protein portions.")
        if "kidney" in h.conditions:
            notes.append("Kidney condition: protein target lowered to the ICMR RDA; do not raise it without medical advice.")
        return notes

    # ---------- edits ----------
    def swap(self, h: Household, plan: dict, day: int, slot: str, avoid: str | None = None) -> dict:
        """Regenerate one meal: e.g. swap(day=3, slot='lunch', avoid='fish')."""
        if avoid in h.allow:
            h.allow[avoid] = False
        fresh = self.build_plan(h)
        plan["days"][day - 1] = fresh["days"][day - 1]
        plan["grocery"] = self.grocery(h, plan["days"])
        return plan
