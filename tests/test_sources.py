import json
from datetime import datetime

from conftest import FIXTURES

import pytest

from air_alert.sources import (
    ParseError,
    classify_official,
    parse_rcb_detail,
    parse_rcb_list,
    parse_rso,
    rcb_article_status,
    scope,
    voivodeship_from_text,
)


def test_rcb_list_keeps_only_air_alerts():
    items = parse_rcb_list((FIXTURES / "rcb_list.html").read_text(encoding="utf-8"))
    titles = [i["title"] for i in items]
    assert "Alert RCB - Zagrożenie z powietrza (28.09)" in titles
    assert items[0] == {"date": "2026-09-28", "url": "/web/rcb/zagrozenie-z-powietrza-2809",
                        "title": "Alert RCB - Zagrożenie z powietrza (28.09)"}
    assert not any("woda" in t.lower() or "MSWiA" in t for t in titles)


def test_rcb_detail_newest_revision_is_cancellation():
    revs = parse_rcb_detail((FIXTURES / "rcb_detail.html").read_text(encoding="utf-8"))
    assert len(revs) == 3
    assert "Odwołano" in revs[0]
    assert "Znajdź bezpieczne miejsce" in revs[1]
    st = rcb_article_status(revs, "lubelskie")
    assert st["kind"] == "cancelled" and st["scope"] == "local"
    # the superseded revisions classify on their own as alarm / warning
    assert classify_official(revs[1]) == "alarm"
    assert classify_official(revs[2]) == "warning"
    assert rcb_article_status(revs, "mazowieckie")["scope"] == "other"


def test_classify_official_edge_cases():
    assert classify_official("Ryzyko przekroczenia PM10 w powietrzu, jakość powietrza zła") is None
    assert classify_official("ĆWICZENIE: trening syren, alarm powietrzny") == "exercise"
    assert classify_official("Alarm powietrzny obowiązuje do odwołania") == "alarm"
    assert classify_official("Zaobserwowano drona nad gminą") == "warning"


def test_scope_does_not_confuse_similar_names():
    assert scope("na terenie woj. zachodniopomorskiego", "pomorskie") == "other"
    assert scope("na terenie woj. dolnośląskiego", "slaskie") == "other"
    assert scope("na terenie woj. wielkopolskiego", "opolskie") == "other"
    assert scope("woj. pomorskiego i warmińsko-mazurskiego", "pomorskie") == "local"
    assert scope("zagrożenie na terenie całego kraju", "lubelskie") == "local"
    assert scope("powiat biłgorajski", "lubelskie") == "unknown"
    assert voivodeship_from_text("województwo lubelskie") == "lubelskie"


def test_rso_fixture_has_no_air_threats():
    data = json.loads((FIXTURES / "rso_lubelskie.json").read_text(encoding="utf-8"))
    assert parse_rso(data, datetime(2026, 9, 28, 18, 0)) == []


def test_rso_validity_window():
    data = {"newses": [{"id": 1, "title": "Alarm powietrzny", "shortcut": "Ukryj się",
                        "content": "", "valid_from": "2026-09-28 17:00:00", "valid_to": "2026-09-28 19:00:00"}]}
    assert parse_rso(data, datetime(2026, 9, 28, 18, 0))[0]["kind"] == "alarm"
    assert parse_rso(data, datetime(2026, 9, 28, 19, 1)) == []


@pytest.mark.parametrize(("text", "kind"), [
    ("Nie ma zagrożenia atakiem z powietrza.", "cancelled"),
    ("ZAKOŃCZYŁ SIĘ ATAK z powietrza. BRAK ZAGROŻENIA.", "cancelled"),
    ("Rosyjski atak z powietrza na Ukrainę. Sytuacja w Polsce jest monitorowana.", "warning"),
    ("W ramach ćwiczeń zostanie ogłoszony alarm powietrzny.", "exercise"),
    ("Próbny alarm powietrzny o 12:00.", "exercise"),
    ("Alarm powietrzny. To nie są ćwiczenia. Ukryj się.", "alarm"),
    ("Alarm powietrzny obowiązuje do  odwołania.", "alarm"),
])
def test_classify_review_cases(text, kind):
    assert classify_official(text) == kind


def _article(body):
    return f'<article><div class="editor-content extra">{body}</div></article>'


def test_rcb_detail_hr_separator_and_uppercase_heading():
    page = _article("<p><b>AKTUALIZACJA!</b></p><p>Zagrożenie atakiem z powietrza. Znajdź bezpieczne miejsce."
                    " woj. lubelskie</p><hr/><p>Odwołano zagrożenie z powietrza. woj. lubelskie</p>")
    revs = parse_rcb_detail(page)
    assert len(revs) == 2
    assert rcb_article_status(revs, "lubelskie")["kind"] == "alarm"


def test_rcb_cancel_for_neighbour_keeps_our_alarm():
    revs = ["Odwołano zagrożenie z powietrza. Alert wysłany na terenie woj. lubelskiego.",
            "Zagrożenie atakiem z powietrza. Znajdź bezpieczne miejsce. "
            "Alert wysłany na terenie woj. lubelskiego i podkarpackiego."]
    assert rcb_article_status(revs, "podkarpackie")["kind"] == "alarm"
    assert rcb_article_status(revs, "lubelskie")["kind"] == "cancelled"
    assert rcb_article_status(revs, "mazowieckie")["scope"] == "other"


def test_rcb_markup_change_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_rcb_detail("<article><div class='body'>x</div></article>")
    with pytest.raises(ParseError):
        parse_rcb_list("<html>redesigned</html>")


def test_rso_newest_message_supersedes():
    def msg(i, created, text):
        return {"id": i, "title": "ALERT RCB", "shortcut": "ALERT RCB", "content": text,
                "valid_from": created, "valid_to": "2026-09-28 23:59:00", "created_at": created}
    info = msg(1, "2026-09-28 18:51:00", "UWAGA! Rosyjski atak powietrzny na terenie Ukrainy. Sytuacja jest monitorowana.")
    alarm = msg(2, "2026-09-28 19:10:00", "Zagrożenie atakiem z powietrza. Znajdź bezpieczne miejsce.")
    cancel = msg(3, "2026-09-28 20:00:00", "Odwołano zagrożenie atakiem z powietrza.")
    at = lambda h, m: datetime(2026, 9, 28, h, m)
    assert [i["kind"] for i in parse_rso({"newses": [info]}, at(19, 0))] == ["warning"]
    assert [i["kind"] for i in parse_rso({"newses": [info, alarm]}, at(19, 30))] == ["alarm"]
    assert [i["kind"] for i in parse_rso({"newses": [cancel, alarm, info]}, at(20, 5))] == ["cancelled"]
