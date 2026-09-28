# Air Alert PL

Integracja Home Assistant (HACS) pokazująca poziom zagrożenia z powietrza dla wybranej
lokalizacji (domyślnie dom z HA). Łączy oficjalne komunikaty z Polski z danymi OSINT
z Ukrainy i wyraźnie je rozróżnia.

> **Uwaga:** to nie jest system ostrzegania. Dane OSINT mogą być niedokładne lub opóźnione.
> Zawsze stosuj się do oficjalnych komunikatów (Alert RCB, syreny, służby).

## Źródła

| Źródło | Typ | Co daje | Wybór wg lokalizacji |
|---|---|---|---|
| [RCB](https://www.gov.pl/web/rcb/komunikaty) | oficjalne | Alerty RCB "zagrożenie z powietrza" z pełną historią aktualizacji (informacja, alarm, odwołanie) | dopasowanie województwa z treści ("na terenie woj. lubelskiego") |
| [RSO](https://komunikaty.tvp.pl/Info/Integration) | oficjalne | komunikaty wojewódzkie, filtrowane pod kątem zagrożeń z powietrza | feed województwa |
| [NEPTUN](https://neptun.in.ua/developers) | OSINT | pozycje dronów/rakiet nad Ukrainą + oficjalne alarmy ukraińskich obwodów i rejonów | promień od lokalizacji |

Województwo jest zgadywane z lokalizacji (reverse geocoding OpenStreetMap Nominatim, jedno
zapytanie przy konfiguracji) i można je poprawić.

## Poziomy

| Poziom | Kiedy |
|---|---|
| `alarm` | **tylko oficjalnie**: aktualny komunikat RCB/RSO dla Twojego województwa wzywający do działania ("Znajdź bezpieczne miejsce", "alarm powietrzny") **i** śledzone zagrożenie w promieniu obserwacji leci w Twoją stronę (kurs ±45° od kierunku na dom) |
| `warning` | oficjalny alarm bez zagrożenia lecącego w Twoją stronę, oficjalny komunikat informacyjny (np. "atak na Ukrainę, sytuacja monitorowana") z zagrożeniem lecącym w Twoją stronę, **lub** OSINT: zagrożenie w promieniu ostrzeżenia (100 km, po odjęciu niepewności pozycji) albo szacowane ETA do promienia alarmu < 30 min |
| `watch` | oficjalny komunikat informacyjny bez zagrożenia lecącego w Twoją stronę, OSINT: zagrożenie w promieniu obserwacji (300 km), alarm w ukraińskim rejonie/obwodzie w promieniu 150 km, oficjalny komunikat bez określonego obszaru lub bez aktualizacji dłużej niż 3 h |
| `safe` | brak znanych zagrożeń - to nie jest gwarancja bezpieczeństwa |

Zasady:
- Alert RCB trafia do całego województwa, także gdy drony lecą gdzie indziej. Dlatego oficjalny poziom
  jest obniżany o jeden stopień (alarm -> warning, informacja -> watch), dopóki żaden śledzony tor nie leci
  w Twoją stronę. Nigdy nie spada do `safe`, a `binary_sensor.air_alert_rcb` pokazuje surowy sygnał oficjalny.
  Wyłączysz to w opcjach ("Oficjalny alarm daje ALARM tylko gdy...").
- **Ograniczenie:** NEPTUN śledzi drony tylko nad Ukrainą (słabo na Wołyniu i we Lwowie). Dron już nad
  Polską zwykle znika z danych, więc przy prawdziwym zagrożeniu poziom może zostać na `warning`.
  Dla krytycznych działań (syrena, budzenie) reaguj też na `binary_sensor.air_alert_rcb`.
- Poziom końcowy = max(oficjalny po korekcie, OSINT).
- OSINT nie daje `alarm`. Ekstrapolacja toru (CPA/ETA) może podnieść tylko do `warning`.
- Tor jest ekstrapolowany tylko przy znanej prędkości (podanej przez NEPTUN albo zmierzonej z naszych
  kolejnych obserwacji). Sam kurs nie wystarcza.
- Wpisy NEPTUN `areaOnly` (tylko nazwa obwodu, bez pozycji) są pomijane w odległościach, `advisory` max `watch`.
- Podniesienie poziomu natychmiast, obniżenie OSINT dopiero gdy niższy poziom utrzyma się 10 min.
- Alert RCB bez aktualizacji dłużej niż 3 h staje się "nieaktualny" (`watch`), a nie `safe`.

Wszystkie promienie i czasy zmienisz w opcjach integracji.

## Encje

| Encja | Opis |
|---|---|
| `sensor.air_alert_level` | `safe` / `watch` / `warning` / `alarm`; atrybuty: `official_level` (surowy), `official_effective` (po korekcie kierunku), `threat_heading_here`, `osint_level`, `reasons`, `official_alerts`, `ua_alerts_near`, `sources` (stan i czas ostatniego pobrania każdego źródła) |
| `binary_sensor.air_alert_rcb` | włączony gdy obowiązuje oficjalny komunikat (RCB/RSO) dla obszaru |
| `sensor.air_alert_closest_threat` | km do najbliższego zagrożenia; atrybuty: typ, namiar, kurs, pewność, niepewność, liczba źródeł, prędkość zbliżania, CPA, ETA |
| `sensor.air_alert_active_threats` | liczba zagrożeń w promieniu obserwacji |
| `sensor.air_alert_approaching_threats` | liczba zbliżających się zagrożeń |
| `sensor.air_alert_eta` | minuty do wejścia najbliższego toru w promień alarmu (szacunek, nie pewna pozycja) |
| `sensor.air_alert_last_update` | czas ostatniej aktualizacji |
| `geo_location.*` | każde zagrożenie w promieniu obserwacji jako punkt na mapie HA |

Historia: zapisuje ją recorder HA (wykresy, logbook). Encje są niedostępne, gdy żadne źródło nie
odpowiada od 10 min.

## Powiadomienia

Przy każdej zmianie poziomu integracja wysyła zdarzenie `air_alert_level_changed`
(`from`, `to`, `official_level`, `osint_level`, `reasons`). Przykład:

```yaml
automation:
  - alias: Air Alert - powiadomienie
    triggers:
      - trigger: event
        event_type: air_alert_level_changed
    actions:
      - action: notify.mobile_app_telefon
        data:
          title: "Zagrożenie: {{ trigger.event.data.to }}"
          message: "{{ trigger.event.data.reasons | join(', ') or 'brak szczegółów' }}"
```

## Instalacja

HACS -> Custom repositories -> URL tego repozytorium, kategoria Integration -> zainstaluj ->
restart HA -> Ustawienia -> Urządzenia i usługi -> Dodaj integrację -> "Air Alert PL".

## Rozwój

```bash
uv run --no-project --with pytest -p 3.13 python -m pytest -q tests   # testy logiki, bez HA
```

Lokalny HA do testów: `I:\Projekty\ha\_dev` (`docker compose up -d`, http://localhost:8123).

## Atrybucja

Dane OSINT: [Mapa powietrznych zagrożeń - NEPTUN](https://neptun.in.ua/) (bezpłatne API, wymagany
widoczny link do źródła). Oficjalne: Rządowe Centrum Bezpieczeństwa, Regionalny System Ostrzegania (TVP).
