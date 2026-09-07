"""A small sentiment word list tuned for chat, and the emoji pattern.

This is a word count, not a mood reading. It has no idea about sarcasm,
negation ("not great"), or context, so the app labels every number it
produces as "tone words", never as how anyone felt.
"""

from __future__ import annotations

import re

POSITIVE = set("""
love loved lovely lovin loving like liked likes awesome amazing great good best better nice
happy glad excited excite stoked thrilled delighted pleased grateful thankful thanks thank thx ty
congrats congratulations proud brilliant perfect wonderful fantastic incredible outstanding excellent
fun funny hilarious lol lmao haha hahaha hehe cute sweet adorable beautiful gorgeous handsome
yes yay woo yay woohoo hooray nice cool dope lit fire epic legend legendary goat king queen
win won winning success succeeded nailed crushed smashed slay yesss
enjoy enjoyed enjoying appreciate appreciated appreciation kind kindness generous
easy smooth clear helpful useful worth calm relaxed relaxing safe healthy strong lucky
please welcome sure definitely absolutely agreed agree deal perfect ok okay fine
miss missed missing hug hugs kiss kisses xoxo friend friends family
""".split())

NEGATIVE = set("""
hate hated hates dislike awful terrible horrible worst bad worse sucks suck sucked sucky
sad unhappy upset angry mad furious annoyed annoying irritated frustrated frustrating
sorry apologies apologize regret guilty ashamed embarrassed embarrassing
tired exhausted exhausting drained sick ill hurt hurts pain painful sore
stress stressed stressful anxious anxiety worried worry scared afraid nervous panic
problem problems issue issues broken broke fail failed failing failure mess messed
no not never cant cannot wont dont didnt nope nah unfortunately sadly
boring bored dull annoyed ugh meh yikes damn crap rude mean cruel toxic
late delayed cancelled canceled lost losing lose missed expensive waste wasted
confused confusing hard difficult impossible awkward weird creepy gross disgusting
argue argument fight fighting angry ignore ignored ghosted blocked
""".split())

# Overlap would double-count; positives win because chat skews friendly.
NEGATIVE -= POSITIVE

_CP = "[\U0001F300-\U0001FAFF\U00002600-\U000026FF\U00002700-\U000027BF⬅-⭕❤❗❕]"
EMOJI = re.compile(
    "[\U0001F1E6-\U0001F1FF]{2}"                       # flags, two regional indicators
    f"|{_CP}[️\U0001F3FB-\U0001F3FF]*"            # a symbol plus modifiers
    f"(?:‍{_CP}[️\U0001F3FB-\U0001F3FF]*)*"  # joined into one glyph
)
