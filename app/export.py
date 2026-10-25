"""Exports: PDF (A4 landscape), CSV and Markdown.

The PDF is English: reportlab cannot shape Tamil script. For a Tamil PDF, render the
Markdown/HTML with a HarfBuzz-based engine such as WeasyPrint (see README).
"""
from __future__ import annotations

import csv
import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

INK = colors.HexColor("#22301f")
LEAF = colors.HexColor("#2f6b3a")
TURMERIC = colors.HexColor("#e9a825")
CREAM = colors.HexColor("#fbf6ea")
LINE = colors.HexColor("#d9cfb8")
MUTED = colors.HexColor("#6b6a5e")
SLOTS = [("breakfast", "Breakfast"), ("lunch", "Lunch"), ("snack", "4:30 snack"), ("dinner", "Dinner")]


def _styles():
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=8.2, leading=10.4, textColor=INK, alignment=TA_LEFT)
    return {
        "base": base,
        "small": ParagraphStyle("s", parent=base, fontSize=7.2, leading=9, textColor=MUTED),
        "head": ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=8.4, textColor=colors.white),
        "h1": ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=LEAF),
        "h2": ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=LEAF, spaceBefore=6, spaceAfter=4),
        "day": ParagraphStyle("d", parent=base, fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=INK),
        "bold": ParagraphStyle("bb", parent=base, fontName="Helvetica-Bold"),
    }


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _page(canvas, doc):
    canvas.saveState()
    w, h = landscape(A4)
    canvas.setFillColor(TURMERIC)
    canvas.rect(0, h - 4 * mm, w, 4 * mm, stroke=0, fill=1)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(12 * mm, 7 * mm, "Ore Samayal - balanced South Indian meal plan (POC). General guidance, not medical advice.")
    canvas.drawRightString(w - 12 * mm, 7 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _grid(data, widths, header=True, zebra=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
          ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), LEAF))
    if zebra:
        for r in range(1 if header else 0, len(data)):
            if r % 2 == 0:
                st.append(("BACKGROUND", (0, r), (-1, r), CREAM))
    t.setStyle(TableStyle(st))
    return t


def to_pdf(plan: dict, path: str | None = None) -> bytes:
    S = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(path or buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title="Ore Samayal meal plan", author="Ore Samayal POC")
    h, t, m = plan["household"], plan["targets"], plan["metrics"]
    W = landscape(A4)[0] - 24 * mm
    story = [Paragraph("Ore Samayal - one kitchen, the whole family", S["h1"]),
             Paragraph(f"{len(plan['days'])}-day balanced South Indian plan · generated {date.today():%d %b %Y}", S["small"]),
             Spacer(1, 5 * mm)]

    fam = [f"{h['adults']} adults"]
    if h["kids_ages"]:
        fam.append("children aged " + ", ".join(map(str, h["kids_ages"])))
    if h["babies_months"]:
        fam.append("baby " + ", ".join(f"{b} months" for b in h["babies_months"]))
    summary = [
        [Paragraph("Family", S["bold"]), Paragraph(_esc("; ".join(fam)), S["base"]),
         Paragraph("Diet", S["bold"]), Paragraph(_esc(h["diet"]), S["base"])],
        [Paragraph("Adult targets", S["bold"]),
         Paragraph(f"Protein ≥ {t['adult_protein_g']} g/day · carbs ≤ {t['adult_carb_pct_max']}% of energy · fibre ≈ {t['adult_fibre_g']} g · oil ≤ {t['oil_tsp_per_adult']} tsp", S["base"]),
         Paragraph("Plan result", S["bold"]),
         Paragraph(f"Protein {m['adult_protein_g'][0]}-{m['adult_protein_g'][1]} g · carbs {m['adult_carb_pct'][0]}-{m['adult_carb_pct'][1]}% · "
                   f"{m['days_meeting_protein']}/{len(plan['days'])} days on protein target", S["base"])],
        [Paragraph("Allergies", S["bold"]), Paragraph(_esc(", ".join(h["allergies"]) or "none noted"), S["base"]),
         Paragraph("Cooking for", S["bold"]), Paragraph(f"{h['portion_factor']} adult portions (child 0.6, baby 0.15)", S["base"])],
    ]
    story += [_grid(summary, [28 * mm, W / 2 - 28 * mm, 28 * mm, W / 2 - 28 * mm], header=False, zebra=False), Spacer(1, 4 * mm)]
    story.append(Paragraph("Every day: one whole fruit (no juice) · 12 g almonds + 8 g walnuts for adults"
                           + ("" if "dairy" in h["allergies"] else " · 100 ml milk")
                           + " · water, buttermilk or tender coconut water as drinks · baby portions lifted before salt and chilli.", S["small"]))
    for w in plan.get("warnings", []):
        story.append(Paragraph("<b>Check:</b> " + _esc(w), S["base"]))
    story.append(Spacer(1, 5 * mm))

    widths = [24 * mm, (W - 24 * mm) * 0.42, (W - 24 * mm) * 0.29, (W - 24 * mm) * 0.29]
    for d in plan["days"]:
        head = ["", "Adults (lower-carb plate)"]
        head.append("Children" if h["kids_ages"] else "")
        head.append("Baby" if h["babies_months"] else "")
        rows = [[Paragraph(x, S["head"]) for x in head]]
        for key, label in SLOTS:
            meal = d["meals"][key]
            rows.append([Paragraph(f"<b>{label}</b>", S["base"]), Paragraph(_esc(meal["adult"]), S["base"]),
                         Paragraph(_esc(meal.get("kids", "")), S["small"]), Paragraph(_esc(meal.get("baby", "")), S["small"])])
        n = d["nutrition"]
        foot = (f"<b>Adult day:</b> {n['kcal']} kcal · protein {n['protein_g']} g · carbs {n['carb_g']} g ({n['carb_pct']}%) · fat {n['fat_g']} g · "
                f"fibre {n['fibre_g']} g &nbsp;&nbsp; <b>Fruit:</b> {d['fruit']} &nbsp; <b>Drink:</b> {d['drink']} &nbsp; <b>Morning:</b> {d['morning_water']}")
        extra = []
        if d["adjustments"]:
            extra.append("<b>Auto-adjusted:</b> " + _esc("; ".join(d["adjustments"])))
        if d["prep_tonight"]:
            extra.append("<b>Tonight for tomorrow:</b> " + _esc(" · ".join(d["prep_tonight"])))
        block = [Paragraph(f"Day {d['day']} · {d['weekday']}", S["day"]), Spacer(1, 1.5 * mm), _grid(rows, widths),
                 Spacer(1, 1.2 * mm), Paragraph(foot, S["small"])]
        block += [Paragraph(e, S["small"]) for e in extra]
        block.append(Spacer(1, 5 * mm))
        story.append(KeepTogether(block))

    story.append(PageBreak())
    for wk in plan["grocery"]:
        story.append(Paragraph(f"Grocery list - week {wk['week']}", S["h2"]))
        story.append(Paragraph("Scaled to the whole household; meat and fish shown as bought (bone and trim included). Buy perishables twice a week.", S["small"]))
        story.append(Spacer(1, 2 * mm))
        items = wk["items"]
        cells = [Paragraph(f"<b>{_esc(i['item'])}</b> - {_esc(i['qty'])}", S["base"]) for i in items]
        cols = 3
        rows = [cells[r:r + cols] + [""] * (cols - len(cells[r:r + cols])) for r in range(0, len(cells), cols)]
        story.append(_grid(rows, [W / cols] * cols, header=False))
        story.append(Spacer(1, 4 * mm))

    story.append(PageBreak())
    story.append(Paragraph("Recipe cards (only the dishes in this plan)", S["h2"]))
    story.append(Paragraph("Quantities in the plan are per adult; multiply by the household's adult portions. "
                           "Poriyal and kootu work with any vegetable in season.", S["small"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_recipes(plan, S, W))
    story.append(Paragraph("Safety notes", S["h2"]))
    story += [Paragraph("• " + _esc(s), S["base"]) for s in plan.get("warnings", []) + plan["safety_notes"]]
    story.append(Paragraph("Sources (retrieved knowledge base)", S["h2"]))
    story += [Paragraph(_esc(s), S["small"]) for s in plan["sources"]]
    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return b"" if path else buf.getvalue()


def _recipes(plan: dict, S, W):
    from .knowledge import load_dishes
    dishes = load_dishes()
    seen, cards = set(), []
    for d in plan["days"]:
        for items in d["item_ids"].values():
            for i in items:
                dish = dishes.get(i)
                if not dish or not dish.get("steps"):
                    continue
                key = tuple(dish["steps"])
                if key in seen:
                    continue
                seen.add(key)
                title = dish["name"].split(" (")[0]
                if i.startswith(("idli_", "dosa_", "uthappam_")):
                    title = {"idli": "Millet idli (batter)", "dosa": "Millet dosa", "uthappam": "Millet uthappam"}[i.split("_")[0]]
                body = "".join(f"<br/>{n}. {_esc(s)}" for n, s in enumerate(dish["steps"], 1))
                cards.append(Paragraph(f"<b>{_esc(title)}</b>{body}", S["small"]))
    cols = 3
    rows = [cards[r:r + cols] + [""] * (cols - len(cards[r:r + cols])) for r in range(0, len(cards), cols)]
    return _grid(rows, [W / cols] * cols, header=False)


def to_csv(plan: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["day", "weekday", "meal", "adults", "children", "baby", "adult_kcal", "adult_protein_g", "adult_carb_pct", "adult_fibre_g", "fruit", "drink"])
    for d in plan["days"]:
        n = d["nutrition"]
        for key, label in SLOTS:
            meal = d["meals"][key]
            w.writerow([d["day"], d["weekday"], label, meal["adult"], meal.get("kids", ""), meal.get("baby", ""),
                        n["kcal"], n["protein_g"], n["carb_pct"], n["fibre_g"], d["fruit"], d["drink"]])
    return out.getvalue()


def to_markdown(plan: dict, tamil: bool = False) -> str:
    h, m = plan["household"], plan["metrics"]
    lines = ["# Ore Samayal meal plan", "",
             f"**Diet:** {h['diet']} · **Adults:** {h['adults']} · **Children:** {', '.join(map(str, h['kids_ages'])) or '-'} · "
             f"**Baby (months):** {', '.join(map(str, h['babies_months'])) or '-'}", "",
             f"Adult protein {m['adult_protein_g'][0]}-{m['adult_protein_g'][1]} g/day · carbs {m['adult_carb_pct'][0]}-{m['adult_carb_pct'][1]}% of energy", ""]
    for d in plan["days"]:
        n = d["nutrition"]
        lines += [f"## Day {d['day']} · {d['weekday']}", "", "| Meal | Adults | Children | Baby |", "|---|---|---|---|"]
        for key, label in SLOTS:
            meal = d["meals"][key]
            adult = meal["tamil"] if tamil else meal["adult"]
            lines.append(f"| {label} | {adult} | {meal.get('kids', '')} | {meal.get('baby', '')} |")
        lines += ["", f"Adult day: {n['kcal']} kcal, protein {n['protein_g']} g, carbs {n['carb_pct']}%, fibre {n['fibre_g']} g. "
                      f"Fruit: {d['fruit']}. Drink: {d['drink']}."]
        if d["prep_tonight"]:
            lines.append("Tonight: " + " · ".join(d["prep_tonight"]))
        lines.append("")
    for wk in plan["grocery"]:
        lines += [f"## Grocery list - week {wk['week']}", ""] + [f"- {i['item']}: {i['qty']}" for i in wk["items"]] + [""]
    lines += ["## Safety notes", ""] + [f"- {s}" for s in plan["safety_notes"]] + ["", "## Sources", ""] + [f"- {s}" for s in plan["sources"]]
    return "\n".join(lines) + "\n"
