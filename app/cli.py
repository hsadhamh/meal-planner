"""Terminal chat.  python -m app.cli            (interactive)
                 python -m app.cli --once "4 adults, 6 year old, 8 month baby, non-veg, 7 day plan"
"""
from __future__ import annotations

import argparse

from .agent import Agent, Session


def main():
    ap = argparse.ArgumentParser(description="Ore Samayal meal-plan agent")
    ap.add_argument("--once", help="send one message and exit")
    ap.add_argument("--session", default="cli")
    args = ap.parse_args()
    agent, s = Agent(), Session(id=args.session)
    print(f"[mode: {'Claude ' + __import__('app.agent', fromlist=['MODEL']).MODEL if agent.client else 'offline rules + retrieval'}]")
    if args.once:
        print(agent.chat(s, args.once))
        return
    print("Ore Samayal. Describe your family, ask a question, or type 'swap day 3 lunch no fish'. Ctrl-D to quit.")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text:
            print("\n" + agent.chat(s, text))


if __name__ == "__main__":
    main()
