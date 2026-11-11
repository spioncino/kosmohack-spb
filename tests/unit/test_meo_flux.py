from datetime import UTC, datetime
from pathlib import Path

import pytest

from evarisk.parsers.meo_flux import parse_meo_flux

FIX = Path("tests/fixtures/meo/flux_data_2024_excerpt.txt")


def test_parses_the_2024_forecast_excerpt() -> None:
    flux = parse_meo_flux(FIX.read_bytes())
    assert flux.year == 2024
    # Заголовок файла NTRS 20230015158: коэффициенты для 6.7 Дж, 105 Дж, 2.83 кДж и 105 кДж.
    assert flux.energies_j == (6.7, 105.0, 2830.0, 105000.0)
    assert flux.column(6.7) == 0 and flux.column(2830.0) == 2
    i = flux.times.index(datetime(2024, 5, 11, 12, tzinfo=UTC))
    # Строка «2024-05-11 12:00:00 … 5.008387e-02 9.166790e-02 2.502002e-01 8.858948e-01», ZHR 12.78.
    assert flux.factors[i, 0] == pytest.approx(0.05008387) and flux.factors[i, 2] == pytest.approx(0.2502002)
    assert flux.zhr[i] == pytest.approx(12.78)
    peak = flux.times.index(datetime(2024, 12, 14, 2, tzinfo=UTC))
    # Геминиды: наибольший коэффициент 2024 года для 6.7 Дж, 0.58126.
    assert flux.factors[peak, 0] == pytest.approx(0.58126)


def test_rejects_other_formats() -> None:
    text = FIX.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="нет года"):
        parse_meo_flux(text.replace("# 2024 meteor shower forecast", "# meteor shower forecast", 1).encode())
    with pytest.raises(ValueError, match="энерги"):
        parse_meo_flux(b"# 2024 forecast\n# no energies\n 2024-01-01 00:00:00 1 2 3 4 5 6 7 8 9 10 11\n")
    body = [line for line in text.splitlines() if not line.startswith("#")]
    head = [line for line in text.splitlines() if line.startswith("#")]
    with pytest.raises(ValueError, match="не возрастают"):
        parse_meo_flux("\n".join([*head, body[1], body[0]]).encode())
    with pytest.raises(ValueError, match="столбцов"):
        parse_meo_flux("\n".join([*head, body[0].rsplit(" ", 1)[0]]).encode())
    with pytest.raises(ValueError, match="нет строк"):
        parse_meo_flux("\n".join(head).encode())
    with pytest.raises(ValueError, match="нет коэффициента"):
        parse_meo_flux(FIX.read_bytes()).column(50.0)
