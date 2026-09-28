"""Constants for Air Alert."""
from datetime import timedelta

DOMAIN = "air_alert"
EVENT_LEVEL_CHANGED = f"{DOMAIN}_level_changed"

CONF_VOIVODESHIP = "voivodeship"
# option key -> default; mirrors classifier.Settings
OPTION_DEFAULTS = {
    "watch_km": 300,
    "warning_km": 100,
    "alarm_km": 30,
    "ua_alert_km": 150,
    "eta_warning_min": 30,
    "downgrade_min": 10,
    "official_ttl_h": 3,
}

SCAN_INTERVAL = timedelta(seconds=30)  # NEPTUN asks for >= 5 s
OFFICIAL_INTERVAL = timedelta(seconds=120)  # RCB / RSO scrape
STALE_AFTER = timedelta(minutes=10)  # no source answered this long -> entities unavailable

NEPTUN = "https://neptun.in.ua"
RCB_LIST = "https://www.gov.pl/web/rcb/komunikaty"
RCB_BASE = "https://www.gov.pl"
RSO_FEED = "https://komunikaty.tvp.pl/komunikatyxml/{}/wszystkie/0?_format=json"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"

ATTRIBUTION = (
    "Official: RCB (gov.pl), RSO (komunikaty.tvp.pl). "
    "OSINT: NEPTUN (https://neptun.in.ua) - information aggregator, not an official alert system."
)
USER_AGENT = "HomeAssistant-AirAlert/0.1"
