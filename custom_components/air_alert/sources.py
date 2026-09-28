"""Parsers for the upstream feeds. Pure: text/JSON in, plain data out."""
from __future__ import annotations

import html
import re
from datetime import datetime

# RSO slug -> (display name, stem used to spot the voivodeship in free text)
VOIVODESHIPS: dict[str, tuple[str, str]] = {
    "dolnoslaskie": ("dolnośląskie", "dolnośląsk"),
    "kujawsko-pomorskie": ("kujawsko-pomorskie", "kujawsko-pomorsk"),
    "lubelskie": ("lubelskie", "lubelsk"),
    "lubuskie": ("lubuskie", "lubusk"),
    "lodzkie": ("łódzkie", "łódzk"),
    "malopolskie": ("małopolskie", "małopolsk"),
    "mazowieckie": ("mazowieckie", "mazowieck"),
    "opolskie": ("opolskie", "opolsk"),
    "podkarpackie": ("podkarpackie", "podkarpack"),
    "podlaskie": ("podlaskie", "podlask"),
    "pomorskie": ("pomorskie", "pomorsk"),
    "slaskie": ("śląskie", "śląsk"),
    "swietokrzyskie": ("świętokrzyskie", "świętokrzysk"),
    "warminsko-mazurskie": ("warmińsko-mazurskie", "warmińsko-mazursk"),
    "wielkopolskie": ("wielkopolskie", "wielkopolsk"),
    "zachodniopomorskie": ("zachodniopomorskie", "zachodniopomorsk"),
}
# the lookbehind keeps "pomorsk" out of "zachodniopomorsk", "śląsk" out of "dolnośląsk", ...
_VOIV_RE = {slug: re.compile(r"(?<![\w-])" + stem) for slug, (_, stem) in VOIVODESHIPS.items()}
_NATIONWIDE = re.compile(r"całego kraju|całej polski|cała polska")

_AIR = re.compile(r"z powietrza|powietrzn|dron|bezzałogow|rakiet")
# "to nie są ćwiczenia" must not turn a real alarm into an exercise
_EXERCISE = re.compile(r"(?<!nie są )(?<!nie jest to )(?:ćwicz|trening|próbn)")
_CANCEL = re.compile(
    r"(?<!do )odwoł|brak zagrożenia|nie ma zagrożenia|zakończył\w* się|koniec zagrożenia"
)  # "do odwołania" = "until further notice", not a cancellation
_PROTECT = re.compile(
    r"znajdź bezpieczne|schron|ukryj się|zagrożenie atakiem|alarm powietrzny|ogłasza się alarm"
)


def classify_official(text: str) -> str | None:
    """Kind of an official air-threat message: alarm | warning | cancelled | exercise.

    None = not about an air threat. alarm = explicit protective action,
    warning = informational (e.g. "attack on Ukraine, situation monitored").
    """
    t = re.sub(r"\s+", " ", text.lower())
    if not _AIR.search(t):
        return None
    if _EXERCISE.search(t):
        return "exercise"
    if _CANCEL.search(t):
        return "cancelled"
    if _PROTECT.search(t):
        return "alarm"
    return "warning"


def mentioned_voivodeships(text: str) -> set[str]:
    t = text.lower()
    return {slug for slug, rx in _VOIV_RE.items() if rx.search(t)}


def scope(text: str, voivodeship: str) -> str:
    """local | other | unknown - does the message target our voivodeship."""
    if _NATIONWIDE.search(text.lower()):
        return "local"
    found = mentioned_voivodeships(text)
    if voivodeship in found:
        return "local"
    return "other" if found else "unknown"


def voivodeship_from_text(text: str) -> str | None:
    """'województwo lubelskie' (e.g. from reverse geocoding) -> 'lubelskie'."""
    found = mentioned_voivodeships(text)
    return found.pop() if len(found) == 1 else None


# ---------------------------------------------------------------- RCB (gov.pl)
_RCB_ITEM = re.compile(
    r'class="date">\s*(\d{2})\.(\d{2})\.(\d{4})\s*</span>.*?<a href="([^"]+)">\s*(.*?)\s*</a>', re.S
)
_RCB_BODY = re.compile(r'<div class="[^"]*\beditor-content\b[^"]*">(.*?)</article>', re.S)
_RCB_SPLIT = re.compile(r"\n\s*-{3,}\s*\n|(?=\n\s*aktualizacja\b)", re.I)


class ParseError(ValueError):
    """Page structure changed - keep the previous data instead of reporting 'no alerts'."""


def parse_rcb_list(page: str) -> list[dict]:
    """List page -> [{date, url, title}] for 'Alert RCB' items about air threats."""
    if 'class="date"' not in page:
        raise ParseError("RCB list: no dated items found")
    items = []
    for d, m, y, url, title in _RCB_ITEM.findall(page):
        title = html.unescape(title)
        if title.startswith("Alert RCB") and _AIR.search(title.lower()):
            items.append({"date": f"{y}-{m}-{d}", "url": url, "title": title})
    return items


def parse_rcb_detail(page: str) -> list[str]:
    """Article page -> revision texts, newest first (gov.pl prepends updates)."""
    m = _RCB_BODY.search(page)
    if not m:
        raise ParseError("RCB article: content block not found")
    body = re.sub(r"<hr[^>]*>", "\n-----\n", m.group(1), flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", body))
    chunks = (re.sub(r"\s+", " ", c).strip() for c in _RCB_SPLIT.split(text))
    return [c for c in chunks if c]


def rcb_article_status(revisions: list[str], voivodeship: str) -> dict:
    """Current state of an RCB article for our voivodeship.

    = the newest revision that is not explicitly about other voivodeships, so a
    cancellation for a neighbour does not cancel an alarm that also covered us.
    """
    mentions_us = scope(" ".join(revisions), voivodeship) == "local"
    for rev in revisions:
        sc = scope(rev, voivodeship)
        if sc != "other":
            return {"kind": classify_official(rev), "text": rev,
                    "scope": "local" if sc == "local" or mentions_us else "unknown"}
    return {"kind": None, "scope": "other", "text": revisions[0] if revisions else ""}


# ---------------------------------------------------------------- RSO (TVP)
def parse_rso(data: dict, now: datetime) -> list[dict]:
    """Current air-threat state from an RSO voivodeship feed. `now` = naive Polish local time.

    Each RCB update arrives as a separate message valid until midnight, so only the
    newest one counts (a later "odwołano" supersedes an earlier alarm). Exercises are skipped.
    """
    out = []
    for n in data.get("newses") or []:
        text = " ".join(filter(None, (n.get("title"), n.get("shortcut"), n.get("content"))))
        kind = classify_official(text)
        if kind is None:
            continue
        try:
            start = datetime.strptime(n["valid_from"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(n["valid_to"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        if start <= now <= end and kind != "exercise":
            out.append({"id": n.get("id"), "title": n.get("title"), "kind": kind,
                        "text": n.get("content") or n.get("shortcut") or n.get("title"),
                        "valid_to": n["valid_to"], "created": n.get("created_at") or n["valid_from"]})
    return sorted(out, key=lambda i: i["created"])[-1:]
