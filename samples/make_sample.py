"""Generate realistic fake exports so the app can be tried without personal data.

    python samples/make_sample.py

Writes ``samples/sample_messages.json`` (four contacts, one file) and
``samples/sample_whatsapp_alex.txt`` (a single WhatsApp-style chat).
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

ME = "Sam"
FRIENDS = {
    "Alex": (0.55, [9, 12, 13, 18, 19, 20, 21, 22, 23]),
    "Jordan": (0.45, [7, 8, 12, 17, 18, 19]),
    "Priya": (0.5, [11, 12, 13, 14, 20, 21]),
    "Marcus": (0.6, [0, 1, 2, 21, 22, 23]),
}
LINES_OK = [
    "hey are you around", "lol yeah", "what time tonight?", "8 works", "did you see the game", "unbelievable",
    "running late sorry", "no worries", "can you send the address", "ok sent", "haha", "that's wild",
    "I think most people would agree", "seventy percent of people drink beer so it's fine", "1 in 5 adults have a tattoo",
    "what are you doing this weekend", "hiking maybe", "sounds good", "brb", "omg", "bring snacks", "on my way",
    "coffee tomorrow?", "definitely", "let me check", "ok cool", "<Media omitted>", "thanks!!", "you're the best",
]
LINES_TYPOS = [
    "definately coming", "recieved it thanks", "seperate cars?", "that was wierd", "occassionally", "tommorow works",
    "untill then", "i beleive so", "goverment stuff", "calender invite sent", "accomodation booked", "wich one",
    "teh best", "alot of people", "neccessary evil", "embarassing", "publically", "truely", "arguement over", "supercede",
]


def build() -> list[dict]:
    rng = random.Random(42)
    start = datetime(2024, 1, 1)
    rows = []
    for friend, (my_share, hours) in FRIENDS.items():
        for _ in range(rng.randint(250, 700)):
            day = start + timedelta(days=rng.randint(0, 365))
            ts = day.replace(hour=rng.choice(hours), minute=rng.randint(0, 59), second=rng.randint(0, 59))
            sender = ME if rng.random() < my_share else friend
            typo_rate = 0.18 if sender == ME else (0.3 if friend == "Marcus" else 0.08)
            line = rng.choice(LINES_TYPOS if rng.random() < typo_rate else LINES_OK)
            rows.append({"contact": friend, "sender": sender, "direction": "sent" if sender == ME else "received",
                         "timestamp": ts.isoformat(), "text": line})
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def main() -> None:
    out = Path(__file__).parent
    rows = build()
    (out / "sample_messages.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    alex = [r for r in rows if r["contact"] == "Alex"]
    lines = []
    for r in alex:
        ts = datetime.fromisoformat(r["timestamp"])
        lines.append(f"{ts.strftime('%-m/%-d/%y, %-I:%M %p')} - {r['sender']}: {r['text']}")
    (out / "sample_whatsapp_alex.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} messages to sample_messages.json and {len(alex)} to sample_whatsapp_alex.txt")


if __name__ == "__main__":
    main()
