"""Live fetch of the Bank of England base rate for statutory interest.

Pulls the official Bank Rate (series IUDBEDR) from the Bank of England's
public Interactive Statistical Database (IADB), so the figures quoted in
Phase 4 messaging track the real published rate without manual updates.
Falls back to settings.boe_base_rate_percent, the last known value, if the
live fetch fails, times out, or returns something unparseable, so a BoE
outage never blocks message generation.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from src.config import settings
from src.utils.retry import resilient_session

logger = logging.getLogger(__name__)

BOE_IADB_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
SERIES_CODE = "IUDBEDR"
CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours, MPC decisions are rare, no need to refetch every call

_cache: dict[str, tuple[float, Decimal]] = {}


def get_boe_base_rate_percent() -> Decimal:
    """Return the current Bank of England base rate, percent.

    Fetches from the BoE's public database, cached for CACHE_TTL_SECONDS.
    Falls back to settings.boe_base_rate_percent (the manually maintained
    value) if the live fetch fails for any reason.
    """
    cached = _cache.get(SERIES_CODE)
    if cached and (time.monotonic() - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        rate = _fetch_boe_base_rate()
        _cache[SERIES_CODE] = (time.monotonic(), rate)
        return rate
    except Exception:
        logger.exception(
            "Failed to fetch live BoE base rate, falling back to configured value %s%%",
            settings.boe_base_rate_percent,
        )
        return Decimal(str(settings.boe_base_rate_percent))


def _fetch_boe_base_rate() -> Decimal:
    """Fetch the latest published Bank Rate from the BoE IADB CSV export."""
    today = date.today()
    date_from = (today - timedelta(days=30)).strftime("%d/%b/%Y")

    session = resilient_session(retries=2, backoff_factor=1.0)
    response = session.get(
        BOE_IADB_URL,
        params={
            "csv.x": "yes",
            "Datefrom": date_from,
            "Dateto": "now",
            "SeriesCodes": SERIES_CODE,
            "CSVF": "TN",
            "UsingCodes": "Y",
            "VPD": "Y",
            "VFD": "N",
        },
        timeout=10,
    )
    response.raise_for_status()

    lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].startswith("DATE"):
        raise ValueError(f"Unexpected BoE IADB response format: {lines[:2]!r}")

    last_row = lines[-1]
    _, _, rate_str = last_row.rpartition(",")
    try:
        return Decimal(rate_str)
    except InvalidOperation as e:
        raise ValueError(f"Could not parse BoE base rate from row: {last_row!r}") from e
