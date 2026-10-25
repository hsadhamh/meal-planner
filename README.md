# Ore Samayal · ஒரே சமையல் — nutrition chat agent (POC)

A chat agent that plans **balanced South Indian family meals**: one kitchen, one cooking session, with
adult, child and baby versions of every meal. The plan downloads as a PDF, CSV or Markdown file.

```
"We are 4 adults, a 6 year old and an 8 month old. We eat chicken, fish and eggs. Make a week plan."
        → 7-day plan · adult protein 82–109 g/day · carbs ≤ 45 % · grocery list · recipe cards · PDF
```

## Why it's built this way

| Layer | Does | Doesn't |
|---|---|---|
| **LLM (Claude)** | Talks with the family (English or Tamil), fills in the household profile, explains choices, cites guidelines | Calculate numbers, invent dishes, give medical advice |
| **RAG** (`knowledge.py`) | Retrieves guideline chunks (ICMR-NIN, WHO, UNICEF, NHS, CDC, FDA) and dish recipes, each with its source | — |
| **Planner** (`planner.py`) | Picks dishes deterministically, follows prep chains, computes nutrition from ingredient grams, auto-adjusts, scales the grocery list | Hallucinate |
| **Export** (`export.py`) | Builds the PDF (A4 landscape), CSV and Markdown | — |

The LLM reaches the rest through five tools: `update_profile`, `search_knowledge`, `generate_plan`, `swap_meal` and `export_plan`.
Without an `ANTHROPIC_API_KEY` the agent switches to an offline mode (regex profile parser plus retrieval), so the POC runs anywhere.

### Planning rules
- **Adult plate (lower-carb):** ½ vegetables, ¼ protein, ¼ grain. Carbs stay at or below 45 % of energy (ICMR 2024). Protein is at least 75 g/day, or the 54 g RDA when kidney disease is noted.
- **Auto-adjust loop:** if carbs are too high, the planner trims lunch grain, then drops the dinner chapathi, then halves the grain, then trims the tiffin. If protein is short, it adds curd, paneer, egg, soya or sprouts at 4:30. Every change is listed in the plan.
- **Prep overlap:**
  - One millet batter covers idli, then dosa, then uthappam.
  - Each pulse pot feeds a snack and the next day's lunch.
  - Lunch chicken or mutton becomes the dinner pepper fry.
  - The planner lists tonight's tasks for tomorrow.
- **Variety:**
  - Each meat, fish or egg appears at most twice a week at lunch.
  - Four millets rotate.
  - Veg sides don't repeat within 3 days.
  - A legume lunch gets a lighter, lower-carb dinner.
- **Children:** the same dishes with less chilli and a full grain portion.
- **Baby (6–12 months):**
  - Each portion is lifted before salt and chilli.
  - No honey, salt, sugar, juice, or cow's milk as a drink.
  - Unsafe dishes, such as sardines and prawns, get substitutes.
- **Allergies** (egg, peanut, sesame, fish, shellfish, dairy, wheat, soy) filter dishes out. If the targets can't be met, the plan says so instead of hiding it.

## Run it

```bash
pip install -r requirements.txt          # reportlab is the only hard dependency
python -m app.cli                        # chat in the terminal
python -m app.cli --once "2 adults, 4 year old, vegetarian, egg ok, peanut allergy, 5 days"
uvicorn app.api:app --reload             # http://localhost:8000 → chat page + PDF download
python -m unittest discover -s tests -t . # 11 tests
export ANTHROPIC_API_KEY=...             # switches on Claude tool use (ORE_MODEL picks the model)
```

API endpoints:
- `POST /chat {session_id?, message}` returns a reply and `pdf_url`.
- `POST /plan` with household JSON returns the full plan JSON.
- `GET /plan/{id}.pdf` returns the PDF.

## Files

```
app/kb/foods.csv        80 foods, per-100 g kcal / protein / fat / carbs / fibre
app/kb/dishes.json      74 South Indian dishes: role, protein type, grams per adult, allergens, kid and baby versions, recipe steps
app/kb/guidelines.md    21 cited guideline chunks (the RAG corpus)
app/knowledge.py        BM25 retrieval (stdlib) + nutrient calculator
app/profile.py          Household model, targets, English/Tamil text parser
app/planner.py          Deterministic planner, auto-adjust loop, grocery list
app/agent.py            Claude tool-use loop + offline fallback
app/export.py           PDF / CSV / Markdown
app/api.py, app/cli.py  FastAPI service and terminal chat
out/sample_plan.*       Sample for 4 adults + 6 y + 8 m, non-veg
```

## From POC to product

1. **Licensed food data.** Replace the approximate values with IFCT 2017 (NIN) or another licensed table, and record the source on every food.
2. **Retrieval.** Swap BM25 for embeddings using pgvector, Chroma or Azure AI Search; `Knowledge.search()` keeps the same contract. Chunk the full ICMR-NIN 2024 guideline PDFs, and add a reranker.
3. **Evaluation.** Build a golden set of around 200 family profiles and check that:
   - no rule is broken (baby, allergies);
   - nutrition targets are met;
   - every claim carries a citation.
   Have a dietitian review a sample every week.
4. **Tamil PDF.** reportlab can't shape Tamil script, so render the Markdown/HTML with WeasyPrint (HarfBuzz) and a Noto Sans Tamil or Mukta Malar font.
5. **Distribution.** A WhatsApp Business bot (send a plan, get the PDF) suits South Indian households better than an app. Add a weekly reminder and a "swap tomorrow's lunch" quick reply.
6. **Safety and compliance.**
   - A clinician signs off on the medical-condition rules.
   - Comply with DPDP Act 2023 consent for family and health data.
   - No diagnosis.
   - An escalation path for faltering growth or allergy reactions.
7. **Personalisation.** Use weights, activity level, what's in the fridge, budget and region (Chettinad, Kongu, Kerala).

*General meal-planning guidance, not medical advice.*
