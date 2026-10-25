"""HTTP API (FastAPI).  Run: uvicorn app.api:app --reload

POST /chat   {"session_id": "abc", "message": "..."}  -> {"reply": "...", "pdf_url": "/plan/abc.pdf" | null}
POST /plan   household JSON (see profile.Household)     -> plan JSON + pdf_url
GET  /plan/{session_id}.pdf                              -> the PDF
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .agent import OUT, Agent, Session
from .profile import PROTEINS, Household

app = FastAPI(title="Ore Samayal meal-plan agent", version="0.1")
agent = Agent()
SESSIONS: dict[str, Session] = {}  # in-memory for the POC; use Redis/Postgres in production


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str


class PlanIn(BaseModel):
    adults: int = Field(2, ge=1, le=12)
    kids_ages: list[int] = []
    babies_months: list[int] = []
    allow: dict[str, bool] = {p: False for p in PROTEINS}
    allergies: list[str] = []
    conditions: list[str] = []
    breastfeeding: bool = False
    days: int = Field(7, ge=1, le=14)
    language: str = "en"


def _session(sid: str | None) -> Session:
    sid = sid or uuid.uuid4().hex[:10]
    if sid not in SESSIONS:
        SESSIONS[sid] = Session(id=sid)
    return SESSIONS[sid]


@app.post("/chat")
def chat(body: ChatIn):
    s = _session(body.session_id)
    reply = agent.chat(s, body.message)
    return {"session_id": s.id, "reply": reply, "pdf_url": f"/plan/{s.id}.pdf" if s.plan else None,
            "mode": "claude" if agent.client else "offline"}


@app.post("/plan")
def plan(body: PlanIn):
    s = _session(None)
    s.household = Household(**body.model_dump())
    s.plan = agent.planner.build_plan(s.household)
    agent.export(s)
    return {"session_id": s.id, "pdf_url": f"/plan/{s.id}.pdf", "plan": s.plan}


@app.get("/plan/{sid}.pdf")
def pdf(sid: str):
    path = OUT / f"plan_{sid}.pdf"
    if not path.exists():
        raise HTTPException(404, "No plan for this session yet")
    return FileResponse(path, media_type="application/pdf", filename="ore-samayal-meal-plan.pdf")


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Ore Samayal chat</title>
<style>body{font:15px system-ui;margin:0;background:#fbf6ea;color:#22301f}main{max-width:720px;margin:auto;padding:16px}
#log{background:#fff;border:1px solid #d9cfb8;border-radius:12px;padding:12px;min-height:300px;white-space:pre-wrap}
.u{color:#2f6b3a;font-weight:600}form{display:flex;gap:8px;margin-top:10px}input{flex:1;padding:10px;border-radius:8px;border:1px solid #d9cfb8}
button{background:#2f6b3a;color:#fff;border:0;border-radius:8px;padding:10px 16px}a{color:#b5730b}</style>
<main><h2>Ore Samayal · ஒரே சமையல்</h2><div id=log>Try: "4 adults, a 6 year old and an 8 month old baby, we eat chicken, fish and eggs. Make a week plan."</div>
<form id=f><input id=m autocomplete=off placeholder="Type a message"><button>Send</button></form></main>
<script>let sid=null;const log=document.getElementById('log');
f.onsubmit=async e=>{e.preventDefault();const t=m.value.trim();if(!t)return;m.value='';
log.innerHTML+='\\n\\n<span class=u>You:</span> '+t.replace(/</g,'&lt;');
const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:t})});
const j=await r.json();sid=j.session_id;log.innerHTML+='\\n\\n'+j.reply.replace(/</g,'&lt;');
if(j.pdf_url)log.innerHTML+='\\n<a href="'+j.pdf_url+'" target=_blank>Download PDF</a>';log.scrollTop=1e9;}</script>"""
