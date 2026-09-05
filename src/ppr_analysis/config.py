"""URLs, filters, and matching thresholds for the local pipeline."""

from __future__ import annotations

PPR_ZIP_URL = (
    "https://www.propertypriceregister.ie/website/npsra/ppr/npsra-ppr.nsf/"
    "Downloads/PPR-ALL.zip/$FILE/PPR-ALL.zip"
)

DAFT_SOLD_BASE = "https://www.daft.ie/sold-properties"
DAFT_GATEWAY_URL = "https://gateway.daft.ie/old/v1/listings"

USER_AGENT = "ppr-analysis-personal/0.1 (local research; cache-first; not for republication)"

DEFAULT_LOCATION = "drogheda-louth"
DEFAULT_PAGE_DELAY_SECONDS = 8.0
DEFAULT_RETRY_AFTER_SECONDS = 45.0
MAX_HTTP_RETRIES = 8
DEFAULT_PAGE_SIZE = 50

# Daft stored-shape id observed on /sold-properties/drogheda-louth
LOCATION_SHAPE_IDS = {
    "drogheda-louth": "3036",
}

DROGHEDA_ESTATES = (
    "wheaton hall",
    "westcourt",
    "ballymakenny",
    "north road",
    "termonfeckin road",
    "termonfeckin rd",
    "the mall",
    "kermon house",
    "college rise",
    "ashfield close",
    "rathmullan",
)

EXACT_FUZZ = 95
HIGH_FUZZ = 88
REVIEW_FUZZ = 75
PRICE_REL_TOLERANCE = 0.02
PRICE_ABS_TOLERANCE = 2500.0
DATE_WINDOW_DAYS = 180

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_SECONDS = 1.1

# Viewbox: west, north, east, south (Nominatim order)
DROGHEDA_VIEWBOX = (-6.45, 53.78, -6.22, 53.68)
DROGHEDA_BOUNDS = {
    "min_lon": -6.45,
    "max_lon": -6.22,
    "min_lat": 53.68,
    "max_lat": 53.78,
}
