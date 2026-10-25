import os
import tempfile
import unittest

from app.agent import Agent, Session
from app.knowledge import Knowledge
from app.planner import Planner
from app.profile import Household, parse_text

KB = Knowledge()
P = Planner(KB)
MEAT = {"chicken", "mutton", "fish", "prawn"}


def proteins_used(plan):
    return {KB.dishes[i]["protein"] for d in plan["days"] for items in d["item_ids"].values() for i in items}


def all_ids(plan):
    return [i for d in plan["days"] for items in d["item_ids"].values() for i in items]


class PlannerTests(unittest.TestCase):
    def test_vegetarian_has_no_meat_or_egg(self):
        plan = P.build_plan(Household(adults=2, kids_ages=[6]))
        self.assertFalse(proteins_used(plan) & (MEAT | {"egg"}))

    def test_eggetarian_has_egg_but_no_meat(self):
        h = Household(adults=2)
        h.allow["egg"] = True
        used = proteins_used(P.build_plan(h))
        self.assertIn("egg", used)
        self.assertFalse(used & MEAT)

    def test_non_veg_uses_each_allowed_meat(self):
        h = parse_text("4 adults, 6 year old, 8 month old baby, non-veg, prawns ok")
        self.assertTrue({"chicken", "mutton", "fish", "prawn", "egg"} <= proteins_used(P.build_plan(h)))

    def test_allergy_filtering(self):
        h = Household(adults=2, allergies=["peanut", "sesame", "wheat"])
        h.allow.update(egg=True, fish=True)
        for i in all_ids(P.build_plan(h)):
            self.assertFalse(set(KB.dishes[i]["allergens"]) & {"peanut", "sesame", "wheat"}, i)

    def test_protein_target_and_carb_cap(self):
        for allow in ({}, {"egg": True}, {"egg": True, "chicken": True, "fish": True, "mutton": True}):
            h = Household(adults=4, kids_ages=[6], babies_months=[8])
            h.allow.update(allow)
            plan = P.build_plan(h)
            for d in plan["days"]:
                self.assertGreaterEqual(d["nutrition"]["protein_g"], plan["targets"]["adult_protein_g"], (allow, d["day"]))
                self.assertLessEqual(d["nutrition"]["carb_pct"], plan["targets"]["adult_carb_pct_max"], (allow, d["day"]))

    def test_baby_safety(self):
        h = Household(adults=2, babies_months=[8])
        h.allow.update(fish=True, prawn=True)
        plan = P.build_plan(h)
        notes = " ".join(plan["safety_notes"]).lower()
        for word in ("honey", "salt", "juice", "cow's milk"):
            self.assertIn(word, notes)
        for d in plan["days"]:
            for meal in d["meals"].values():
                baby = meal["baby"].lower()
                self.assertIn("no salt, sugar or honey", baby)
                self.assertNotIn("prawn", baby)
                self.assertNotIn("sardine", baby)

    def test_batter_reused_across_breakfasts(self):
        plan = P.build_plan(Household(adults=2))
        b = [d["item_ids"]["breakfast"][0] for d in plan["days"]]
        self.assertEqual(b[0].split("_")[1], b[1].split("_")[1])  # idli then dosa, same millet batter

    def test_kidney_lowers_protein_target(self):
        self.assertEqual(Household(conditions=["kidney"]).targets()["adult_protein_g"], 54)

    def test_parser(self):
        h = parse_text("we are 4 adults with a 6 year old and an 8 month old, no mutton, chicken and fish ok, peanut allergy, 14 days")
        self.assertEqual((h.adults, h.kids_ages, h.babies_months, h.days), (4, [6], [8], 14))
        self.assertTrue(h.allow["chicken"] and h.allow["fish"])
        self.assertFalse(h.allow["mutton"])
        self.assertIn("peanut", h.allergies)

    def test_retrieval_finds_baby_honey_rule(self):
        hits = KB.search("can my 8 month old baby have honey", k=3, kind="guideline")
        self.assertTrue(any("honey" in h["text"].lower() for h in hits))


class AgentOfflineTests(unittest.TestCase):
    def test_offline_chat_creates_pdf(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with tempfile.TemporaryDirectory() as tmp:
            import app.agent as ag
            old, ag.OUT = ag.OUT, __import__("pathlib").Path(tmp)
            try:
                a, s = Agent(), Session(id="t")
                reply = a.chat(s, "4 adults, 6 year old, 8 month old baby, non-veg. Make a 7 day plan")
                self.assertIn("Download", reply)
                self.assertTrue(os.path.getsize(os.path.join(tmp, "plan_t.pdf")) > 5000)
                self.assertIn("Day 3 updated", a.chat(s, "swap day 3 lunch no fish"))
                self.assertNotEqual(KB.dishes[s.plan["days"][2]["item_ids"]["lunch"][1]]["protein"], "fish")
            finally:
                ag.OUT = old


if __name__ == "__main__":
    unittest.main()
