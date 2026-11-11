import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

_YEAR = re.compile(r"\b(20\d{2})\b")
_ENERGY = re.compile(r"([0-9.]+e[+-]\d+)\s*J")
# Строка данных: дата, время, юлианская дата, долгота Солнца, ZHR, четыре потока и четыре коэффициента усиления.
_COLUMNS = 13
_FACTOR_COLUMNS = 4


@dataclass(frozen=True)
class MeoFlux:
    """Почасовой прогноз метеорных потоков NASA MEO для низкой орбиты (файл `flux_data.txt` записи NTRS).

    `factors[:, k]` это отношение потока метеорных потоков к спорадическому фону для частиц с кинетической
    энергией не ниже `energies_j[k]`, на площадку, обращённую к неэкранированному радианту.
    """

    year: int
    times: list[datetime]
    zhr: NDArray[np.float64]
    energies_j: tuple[float, ...]
    factors: NDArray[np.float64]

    def column(self, energy_j: float, rel_tol: float = 0.01) -> int:
        """Номер столбца коэффициента для энергии `energy_j` (совпадение с точностью `rel_tol`)."""
        for k, e in enumerate(self.energies_j):
            if abs(e - energy_j) <= rel_tol * energy_j:
                return k
        raise ValueError(f"в прогнозе MEO {self.year} нет коэффициента для {energy_j:g} Дж: {self.energies_j}")


def parse_meo_flux(content: bytes) -> MeoFlux:
    """Разбор `flux_data.txt`: заголовок с годом прогноза, строка энергий, часовые строки данных.

    Формат одинаков у прогнозов 2024–2026 годов: первая строка называет год («# LEO 2026 meteor shower forecast»,
    «# 2024 meteor shower forecast for low Earth orbit»), строка заголовка перечисляет восемь энергий, четыре для
    потоков и четыре для коэффициентов.
    """
    text = content.decode("utf-8")
    header = [line for line in text.splitlines() if line.startswith("#")]
    if not header:
        raise ValueError("в файле прогноза MEO нет заголовка")
    year_match = _YEAR.search(header[0])
    if year_match is None:
        raise ValueError(f"в первой строке прогноза MEO нет года: {header[0]!r}")
    energies: list[float] = []
    for line in header:
        found = [float(x) for x in _ENERGY.findall(line)]
        if found:
            energies = found
    if len(energies) != 2 * _FACTOR_COLUMNS:
        raise ValueError(f"ожидалось восемь энергий в заголовке прогноза MEO, найдено {len(energies)}")
    times: list[datetime] = []
    zhr: list[float] = []
    factors: list[list[float]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != _COLUMNS:
            raise ValueError(f"строка {n} прогноза MEO: {len(parts)} столбцов вместо {_COLUMNS}")
        times.append(datetime.fromisoformat(f"{parts[0]}T{parts[1]}").replace(tzinfo=UTC))
        zhr.append(float(parts[4]))
        factors.append([float(x) for x in parts[_COLUMNS - _FACTOR_COLUMNS :]])
    if not times:
        raise ValueError("в прогнозе MEO нет строк данных")
    if any(b <= a for a, b in pairwise(times)):
        raise ValueError("времена прогноза MEO не возрастают")
    return MeoFlux(
        year=int(year_match.group(1)),
        times=times,
        zhr=np.asarray(zhr, dtype=np.float64),
        energies_j=tuple(energies[_FACTOR_COLUMNS:]),
        factors=np.asarray(factors, dtype=np.float64),
    )
