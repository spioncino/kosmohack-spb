import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from evarisk.store.asof import AsOf
from evarisk.store.backfill import BackfillReport
from evarisk.store.mirror_import import classify, import_mirror
from evarisk.store.raw import RawStore

MIRROR = Path("tests/fixtures/mirror")
SWPC = Path("tests/fixtures/swpc_archive")


def test_import_classifies_and_dates(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    summary = import_mirror(MIRROR, store)
    assert (summary.imported, summary.skipped, summary.unknown_version) == (2, 0, 0)
    three = store.records(source="swpc_archive", product="3day_forecast")[0]
    assert three.published_at == datetime(2024, 5, 10, 0, 30, tzinfo=UTC)
    assert three.published_at_kind == "exact" and three.data_version == "issue 0030"
    assert three.fetched_at == datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
    assert three.url.startswith("https://www.ngdc.noaa.gov/")
    kp = store.records(source="gfz_kp")[0]
    assert kp.data_version == "definitive" and kp.published_at_kind == "derived"
    assert kp.published_at == datetime(2024, 7, 15, 0, 0, tzinfo=UTC)


def test_import_is_idempotent(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    import_mirror(MIRROR, store)
    again = import_mirror(MIRROR, store)
    assert again.imported == 0 and again.skipped == 2


def test_dayind_issued_next_day_with_ut_suffix() -> None:
    content = (SWPC / "20240510dayind.txt").read_bytes()
    c = classify("ncei/space_weather_indices/2024/05/20240510dayind.txt", content)
    assert (c.source, c.product, c.kind, c.data_version) == ("swpc_archive", "dayind", "exact", "daily")
    assert c.published_at == datetime(2024, 5, 11, 18, 16, tzinfo=UTC)


def test_three_day_forecast_of_2023_without_leading_colon_is_dated() -> None:
    content = (SWPC / "202307170030three_day_forecast.txt").read_bytes()
    c = classify("ncei/3day_forecast/2023/07/202307170030three_day_forecast.txt", content)
    assert (c.source, c.product, c.kind, c.data_version) == ("swpc_archive", "3day_forecast", "exact", "issue 0030")
    assert c.published_at == datetime(2023, 7, 17, 0, 30, tzinfo=UTC)


GOES_0510 = "goes18_sgps_avg5m/2024/05/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc"


def test_goes_without_last_modified_keeps_the_next_day_rule_and_says_the_file_time_is_unknown() -> None:
    c = classify(GOES_0510, b"")
    assert (c.source, c.product, c.kind) == ("goes_sgps", "sgps_l2_avg5m", "derived")
    assert c.published_at == datetime(2024, 5, 11, 0, 5, tzinfo=UTC)
    assert c.data_version == "reprocessed v3-0-2"
    assert "следующие сутки 00:05Z" in c.notes and "Last-Modified) неизвестно" in c.notes


def test_goes_availability_is_the_later_of_next_day_and_http_last_modified() -> None:
    # Манифест зеркала: файл за 10 мая 2024 на NCEI с Last-Modified Fri, 17 May 2024 05:06:54 GMT. Прогноз из прошлого
    # с отсечением 11–16 мая его видеть не должен: файла ещё не было.
    c = classify(GOES_0510, b"", datetime(2024, 5, 17, 5, 6, 54, tzinfo=UTC))
    assert c.published_at == datetime(2024, 5, 17, 5, 6, 54, tzinfo=UTC) and c.kind == "derived"
    assert "по HTTP Last-Modified файла 2024-05-17T05:06:54+00:00" in c.notes
    # Last-Modified раньше следующих суток 00:05Z не делает файл доступным раньше, чем кончились сутки наблюдений.
    early = classify(GOES_0510, b"", datetime(2024, 5, 10, 23, 0, tzinfo=UTC))
    assert early.published_at == datetime(2024, 5, 11, 0, 5, tzinfo=UTC)
    assert "по правилу «следующие сутки 00:05Z»" in early.notes and "2024-05-10T23:00:00+00:00" in early.notes


def goes_mirror(root: Path, files: dict[str, str]) -> Path:
    """Зеркало с суточными файлами GOES: имя файла и заголовок Last-Modified из манифеста data/mirror."""
    mirror = root / "mirror"
    lines = []
    for i, (name, last_modified) in enumerate(files.items()):
        rel = f"goes18_sgps_avg5m/2024/05/{name}"
        (mirror / rel).parent.mkdir(parents=True, exist_ok=True)
        (mirror / rel).write_bytes(name.encode())
        entry = manifest_entry(rel, f"2026-09-18T18:13:{27 + i:02d}+00:00")
        entry["last_modified"] = last_modified
        entry["content_type"] = "application/x-netcdf"
        lines.append(json.dumps(entry))
    (mirror / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mirror


def test_reimport_amends_goes_records_dated_by_the_next_day_rule(tmp_path: Path) -> None:
    name_09 = "sci_sgps-l2-avg5m_g18_d20240509_v3-0-2.nc"
    name_10 = "sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc"
    mirror = goes_mirror(tmp_path, {name_09: "Thu, 16 May 2024 06:28:14 GMT", name_10: "Fri, 17 May 2024 05:06:54 GMT"})
    store = RawStore(tmp_path / "raw")
    # Запись за 10 мая импортирована прежним правилом: доступность 11 мая 00:05.
    old, _ = store.put(
        source="goes_sgps",
        product="sgps_l2_avg5m",
        url=f"https://x.invalid/goes18_sgps_avg5m/2024/05/{name_10}",
        content=name_10.encode(),
        fetched_at=datetime(2026, 9, 18, 18, 13, 28, tzinfo=UTC),
        published_at=datetime(2024, 5, 11, 0, 5, tzinfo=UTC),
        published_at_kind="derived",
        content_type="application/x-netcdf",
        data_version="reprocessed v3-0-2",
        notes="D19/D22: переобработанный научный ряд, отсчёты были доступны в реальном времени",
    )
    summary = import_mirror(mirror, store)
    assert (summary.imported, summary.skipped, summary.amended) == (1, 0, 1)
    amended = store.get(old.id)
    assert amended.published_at == datetime(2024, 5, 17, 5, 6, 54, tzinfo=UTC)
    assert amended.published_at_kind == "derived"
    assert "по HTTP Last-Modified файла 2024-05-17T05:06:54+00:00" in amended.notes
    assert "прежняя доступность 2024-05-11T00:05:00+00:00" in amended.notes
    by_day = {r.url.rsplit("/", 1)[-1]: r.published_at for r in store.records("goes_sgps")}
    assert by_day == {
        name_09: datetime(2024, 5, 16, 6, 28, 14, tzinfo=UTC),
        name_10: datetime(2024, 5, 17, 5, 6, 54, tzinfo=UTC),
    }
    # Отсечение 11 мая 12:00 не видит ни одного из двух файлов, 16 мая 12:00 видит только файл за 9 мая.
    assert AsOf(store, datetime(2024, 5, 11, 12, 0, tzinfo=UTC)).records("goes_sgps") == []
    seen = AsOf(store, datetime(2024, 5, 16, 12, 0, tzinfo=UTC)).records("goes_sgps")
    assert [r.url.rsplit("/", 1)[-1] for r in seen] == [name_09]
    again = import_mirror(mirror, store)
    assert (again.imported, again.skipped, again.amended) == (0, 2, 0)


def test_ncei_without_issued_is_unknown_and_counted(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    rel = "ncei/geoalerts/2024/05/20240510GEOA.txt"
    (mirror / rel).parent.mkdir(parents=True)
    (mirror / rel).write_text(":Product: 0510GEOA.txt\n# no issue line\n", encoding="utf-8")
    entry = {
        "url": "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/geoalerts/2024/05/20240510GEOA.txt",
        "path": rel,
        "fetched_at": "2026-09-18T18:00:02+00:00",
        "sha256": "z",
        "size": 1,
        "status": 200,
        "last_modified": None,
        "etag": None,
        "content_type": "text/plain",
    }
    (mirror / "manifest.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    store = RawStore(tmp_path / "raw")
    summary = import_mirror(mirror, store)
    assert (summary.imported, summary.skipped, summary.unknown_version) == (1, 0, 1)
    rec = store.records(source="swpc_archive", product="geoalert")[0]
    assert rec.published_at is None and rec.published_at_kind == "unknown"
    assert rec.data_version == "no-issued"


def test_meo_forecast_gets_ntrs_distribution_date() -> None:
    from evarisk.store.mirror_import import classify

    c = classify("meteors/LEO_Forecast_2024.pdf", b"%PDF-1.5 stub")
    assert c.source == "meteor_docs" and c.product == "LEO_Forecast_2024"
    assert c.kind == "derived" and c.published_at == datetime(2023, 11, 2, 23, 59, tzinfo=UTC)
    assert "2023-11-02" in c.data_version
    # Почасовые данные прогноза 2026 года: запись NTRS 20250009524, распространение 17 октября 2025.
    flux = classify("meteors/meo_2026_flux_data.txt", b"# LEO 2026 meteor shower forecast")
    assert flux.product == "meo_flux_2026" and flux.published_at == datetime(2025, 10, 17, 23, 59, tzinfo=UTC)
    assert "NTRS 20250009524" in flux.data_version
    # Прогноз года без машиночитаемых данных в реестре не значится: такой файл это ошибка, а не молчаливый пропуск.
    with pytest.raises(ValueError, match="2023"):
        classify("meteors/meo_2023_flux_data.txt", b"# 2023")


def test_oem_availability_is_the_later_of_creation_and_s3_last_modified() -> None:
    # Файл с CREATION_DATE 2024-05-10T18:51:53 лежит в папке 2024-05-12, S3 Last-Modified 2024-05-13T03:06:46:
    # в публичном бакете его нельзя было скачать раньше 13 мая, отсечение 11 мая его видеть не должно.
    content = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes()
    late = classify("nasa_oem/2024-05-12/ISS.OEM_J2K_EPH.txt", content, datetime(2024, 5, 13, 3, 6, 46, tzinfo=UTC))
    assert late.published_at == datetime(2024, 5, 13, 3, 6, 46, tzinfo=UTC) and late.kind == "exact"
    assert "S3 Last-Modified" in late.notes and "CREATION_DATE" in late.notes
    same_day = classify("nasa_oem/2024-05-12/ISS.OEM_J2K_EPH.txt", content, datetime(2024, 5, 10, 12, 0, tzinfo=UTC))
    assert same_day.published_at is not None and same_day.published_at.isoformat().startswith("2024-05-10T18:51:53")
    unknown = classify("nasa_oem/2024-05-12/ISS.OEM_J2K_EPH.txt", content)
    assert unknown.published_at == same_day.published_at and "Last-Modified неизвестен" in unknown.notes


def test_reimport_amends_availability_of_existing_records_and_keeps_a_trace(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    content = Path("tests/fixtures/nasa_oem/2024-05-12_ISS.OEM_J2K_EPH.txt").read_bytes()
    url = "https://x.invalid/iss-coords/2024-05-12/ISS_OEM/ISS.OEM_J2K_EPH.txt"
    old, _ = store.put(
        source="nasa_oem",
        product="oem",
        url=url,
        content=content,
        fetched_at=datetime(2026, 9, 18, tzinfo=UTC),
        published_at=datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC),
        published_at_kind="exact",
        content_type="text/plain",
        data_version="oem",
    )
    amended = store.amend_availability(old.id, datetime(2024, 5, 13, 3, 6, 46, tzinfo=UTC), "exact", "S3 Last-Modified")
    assert amended.published_at == datetime(2024, 5, 13, 3, 6, 46, tzinfo=UTC)
    assert "прежняя доступность 2024-05-10T18:51:53" in amended.notes
    reloaded = RawStore(tmp_path).get(old.id)
    assert reloaded == amended and RawStore(tmp_path).read(old.id) == content


def manifest_entry(rel: str, fetched_at: str) -> dict[str, object]:
    return {
        "url": f"https://x.invalid/{rel}",
        "path": rel,
        "fetched_at": fetched_at,
        "sha256": "z",
        "size": 1,
        "status": 200,
        "last_modified": None,
        "etag": None,
        "content_type": "text/plain",
    }


def test_unrecognized_manifest_lines_are_skipped_and_counted(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    good = "ncei/3day_forecast/2024/05/202405100030three_day_forecast.txt"
    stub = "donki/notifications_2024-05-09_2024-05-12.json"
    unknown = "ncei/solar_wind/2024/05/20240510swind.txt"
    absent = "ncei/3day_forecast/2024/05/missing.txt"
    for rel, content in (
        (good, (SWPC / "202405100030three_day_forecast.txt").read_bytes()),
        (stub, b"<html><head><title>503 Service Unavailable</title></head></html>"),
        (unknown, b":Issued: 2024 May 10 0030 UTC\n"),
    ):
        (mirror / rel).parent.mkdir(parents=True, exist_ok=True)
        (mirror / rel).write_bytes(content)
    lines = [
        json.dumps(manifest_entry(stub, "2026-09-18T18:00:00+00:00")),
        json.dumps(manifest_entry(good, "2026-09-18T18:00:01+00:00")),
        json.dumps(manifest_entry(unknown, "2026-09-18T18:00:02+00:00")),
        json.dumps(manifest_entry(absent, "2026-09-18T18:00:03+00:00")),
    ]
    (mirror / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = RawStore(tmp_path / "raw")
    summary = import_mirror(mirror, store)
    assert (summary.imported, summary.skipped, summary.unrecognized) == (1, 0, 3)
    assert [name.split(":")[0] for name in summary.unrecognized_names] == [stub, unknown, absent]
    assert "ValueError" in summary.unrecognized_names[0] and "KeyError" in summary.unrecognized_names[1]
    assert "FileNotFoundError" in summary.unrecognized_names[2]
    three = store.records(source="swpc_archive", product="3day_forecast")[0]
    assert three.published_at == datetime(2024, 5, 10, 0, 30, tzinfo=UTC)
    assert store.records(source="donki") == []


def test_backfill_report_names_unrecognized_files() -> None:
    report = BackfillReport(
        start=date(2024, 7, 12),
        end=date(2024, 7, 15),
        new_files=4,
        imported=3,
        failed=(),
        still_missing=(),
        unrecognized=2,
        unrecognized_names=("donki/x.json: ValueError: неизвестный формат",),
    )
    notes = " | ".join(report.notes())
    assert "файлов зеркала не распознано: 2, первые: donki/x.json: ValueError: неизвестный формат" in notes
