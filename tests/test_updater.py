"""Tests für den Updater mit ``httpx.MockTransport`` (kein echter Netzwerkzugriff)."""

import dataclasses
import gzip
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.errors import DownloadError
from lego_mosaik.sources.updater import CatalogUpdater, FileStatus

NEW_CONTENT = b"id,name\n1,neu\n"
OLD_CONTENT = gzip.compress(b"id,name\n1,alt\n")
type Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def config(tmp_path: Path) -> SourceConfig:
    return SourceConfig(data_dir=tmp_path / "rebrickable", files=("colors.csv.gz",))


def _serve(requests: list[httpx.Request]) -> Handler:
    """Handler, der jede Anfrage protokolliert und eine gültige gzip-Datei liefert."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=gzip.compress(NEW_CONTENT))

    return handler


def _write_old_file(config: SourceConfig, age_days: float = 0) -> Path:
    """Legt eine alte Datei an und setzt ihr Änderungsdatum ``age_days`` zurück."""
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / "colors.csv.gz"
    path.write_bytes(OLD_CONTENT)
    timestamp = time.time() - age_days * 24 * 60 * 60
    os.utime(path, (timestamp, timestamp))  # setzt Zugriffs- und Änderungszeit
    return path


def test_downloads_missing_file_with_user_agent(config: SourceConfig) -> None:
    requests: list[httpx.Request] = []
    report = CatalogUpdater(config, httpx.MockTransport(_serve(requests))).update()
    assert report.files[0].status is FileStatus.DOWNLOADED
    assert gzip.decompress((config.data_dir / "colors.csv.gz").read_bytes()) == NEW_CONTENT
    assert str(requests[0].url) == "https://cdn.rebrickable.com/media/downloads/colors.csv.gz"
    assert requests[0].headers["User-Agent"].startswith("lego-mosaik/")


def test_fresh_file_is_not_downloaded(config: SourceConfig) -> None:
    _write_old_file(config, age_days=1)
    requests: list[httpx.Request] = []
    report = CatalogUpdater(config, httpx.MockTransport(_serve(requests))).update()
    assert report.files[0].status is FileStatus.UP_TO_DATE
    assert requests == []


def test_stale_file_is_downloaded(config: SourceConfig) -> None:
    _write_old_file(config, age_days=config.max_age_days + 1)
    requests: list[httpx.Request] = []
    report = CatalogUpdater(config, httpx.MockTransport(_serve(requests))).update()
    assert report.downloaded_any
    assert len(requests) == 1


def test_force_downloads_fresh_file(config: SourceConfig) -> None:
    _write_old_file(config)
    requests: list[httpx.Request] = []
    CatalogUpdater(config, httpx.MockTransport(_serve(requests))).update(force=True)
    assert len(requests) == 1


class _BrokenStream(httpx.SyncByteStream):
    """Liefert ein paar Bytes und bricht dann mit einem Netzwerkfehler ab."""

    def __iter__(self) -> Iterator[bytes]:
        yield gzip.compress(NEW_CONTENT)[:10]
        raise httpx.ReadError("Verbindung abgebrochen")


def test_aborted_download_keeps_old_file(config: SourceConfig) -> None:
    path = _write_old_file(config)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=_BrokenStream()))
    report = CatalogUpdater(config, transport).update(force=True)
    assert report.files[0].status is FileStatus.FAILED
    assert report.files[0].usable
    assert path.read_bytes() == OLD_CONTENT
    assert list(config.data_dir.iterdir()) == [path]  # keine .tmp-Reste
    report.raise_if_unusable()  # alte Datei ist noch da → kein Fehler


def test_truncated_gzip_is_rejected(config: SourceConfig) -> None:
    path = _write_old_file(config)
    truncated = gzip.compress(NEW_CONTENT * 100)[:-12]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=truncated))
    report = CatalogUpdater(config, transport).update(force=True)
    assert report.files[0].status is FileStatus.FAILED
    assert "unvollständig" in report.files[0].message
    assert path.read_bytes() == OLD_CONTENT


def test_http_error_without_old_file_is_unusable(config: SourceConfig) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(404))
    report = CatalogUpdater(config, transport).update()
    assert report.failed[0].message == "Server antwortete mit Status 404."
    with pytest.raises(DownloadError, match="Teilekatalog nicht verfügbar"):
        report.raise_if_unusable()


def test_network_error_gives_german_message(config: SourceConfig) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("keine Verbindung", request=request)

    config = dataclasses.replace(config, files=("colors.csv.gz", "parts.csv.gz"))
    report = CatalogUpdater(config, httpx.MockTransport(refuse)).update()
    assert [update.status for update in report.files] == [FileStatus.FAILED] * 2
    assert "Internetverbindung" in report.files[0].message
