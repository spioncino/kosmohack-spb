from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from evarisk.parsers.goes_sgps import integral_flux_pfu, read_sgps
from evarisk.parsers.sep_list import parse_sep_page

FIX = Path("tests/fixtures/goes/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc")
SEP_PAGE = Path("tests/fixtures/swpc_spe/sep_page_code.html")
OFFICIAL_PEAK_PFU = 208.0
OFFICIAL_PEAK_TIME = datetime(2024, 5, 10, 17, 45, tzinfo=UTC)
TOLERANCE_FACTOR = 1.5
TOLERANCE_MINUTES = 45


def test_peak_of_2024_05_10_within_tolerance_of_official_208_pfu() -> None:
    day = read_sgps(FIX)
    p10 = integral_flux_pfu(day, 10.0)
    i = int(np.nanargmax(p10))
    assert OFFICIAL_PEAK_PFU / TOLERANCE_FACTOR <= p10[i] <= OFFICIAL_PEAK_PFU * TOLERANCE_FACTOR
    assert abs((day.times[i] - OFFICIAL_PEAK_TIME).total_seconds()) <= TOLERANCE_MINUTES * 60


def test_official_reference_comes_from_the_event_list() -> None:
    events = {e.begin: e for e in parse_sep_page(SEP_PAGE.read_text(encoding="utf-8", errors="replace"))}
    may10 = events[datetime(2024, 5, 10, 13, 35, tzinfo=UTC)]
    assert may10.peak_pfu == OFFICIAL_PEAK_PFU and may10.maximum == OFFICIAL_PEAK_TIME
