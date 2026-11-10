import hashlib
import io
import random
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from kyc.cli import main
from kyc.data import ChecksumError, download, extract, notice

URL = "https://x.test/f.tgz"


def _tgz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _part_file(data_dir: Path, data: bytes) -> Path:
    """A FantasyID.tgz.part left behind by an earlier run that stopped."""
    part = data_dir / "raw" / "FantasyID.tgz.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(data)
    return part


def _first_byte(request: httpx.Request) -> int:
    """Where a "Range: bytes=N-" request asks the answer to start."""
    return int(request.headers["range"].removeprefix("bytes=").removesuffix("-"))


class _DropsAfter(httpx.SyncByteStream):
    """A response body that sends some bytes and then loses the connection."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):
        yield self.data
        raise httpx.ReadError("connection dropped")


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _no_download(*args, **kwargs):
    raise AssertionError("downloaded before the notice was accepted")


def test_download_checks_the_published_md5(tmp_path):
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    good = hashlib.md5(body).hexdigest()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    path = download(tmp_path, client=client, url=URL, expected_md5=good)
    assert path.read_bytes() == body
    assert (tmp_path / "raw" / ".gitignore").read_text() == "*\n"
    (tmp_path / "raw" / "FantasyID.tgz").unlink()
    with pytest.raises(ChecksumError):
        download(tmp_path, client=client, url=URL, expected_md5="0" * 32)
    assert not (tmp_path / "raw" / "FantasyID.tgz").exists()
    assert not (tmp_path / "raw" / "FantasyID.tgz.part").exists()


def test_download_reuses_a_verified_archive(tmp_path):
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    dest = tmp_path / "raw" / "FantasyID.tgz"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(body)

    def no_network(request):
        raise AssertionError("download fetched an archive that was already verified")

    client = httpx.Client(transport=httpx.MockTransport(no_network))
    good = hashlib.md5(body).hexdigest()
    path = download(tmp_path, client=client, url=URL, expected_md5=good)
    assert path == dest
    assert dest.read_bytes() == body


def test_an_interrupted_download_resumes_where_it_stopped(tmp_path):
    # Bigger than the 1 MiB pieces download() writes, so the dropped run saves some bytes.
    body = random.Random(0).randbytes(3 << 20)
    good = hashlib.md5(body).hexdigest()
    ranges = []

    def server(request):
        ranges.append(request.headers.get("range"))
        if len(ranges) == 1:
            return httpx.Response(200, stream=_DropsAfter(body[: 2 << 20]))
        return httpx.Response(206, content=body[_first_byte(request) :])

    client = httpx.Client(transport=httpx.MockTransport(server))
    with pytest.raises(httpx.ReadError):
        download(tmp_path, client=client, url=URL, expected_md5=good)
    part = tmp_path / "raw" / "FantasyID.tgz.part"
    saved = part.read_bytes()
    assert saved
    assert body.startswith(saved)

    progress = []
    path = download(
        tmp_path, client=client, url=URL, expected_md5=good, on_progress=progress.append
    )
    assert ranges == [None, f"bytes={len(saved)}-"]
    assert path.read_bytes() == body
    assert progress[0] > len(saved)
    assert progress[-1] == len(body)
    assert not part.exists()


def test_download_starts_over_when_the_server_ignores_the_range(tmp_path):
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    stale = b"bytes from an older run"
    _part_file(tmp_path, stale)
    ranges = []

    def server(request):
        ranges.append(request.headers.get("range"))
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(server))
    good = hashlib.md5(body).hexdigest()
    progress = []
    path = download(
        tmp_path, client=client, url=URL, expected_md5=good, on_progress=progress.append
    )
    assert ranges == [f"bytes={len(stale)}-"]
    assert path.read_bytes() == body
    assert progress == [len(body)]


def test_download_starts_over_when_the_part_file_cannot_be_resumed(tmp_path):
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    too_long = body + b"extra"
    _part_file(tmp_path, too_long)
    ranges = []

    def server(request):
        ranges.append(request.headers.get("range"))
        if "range" in request.headers:
            return httpx.Response(416)
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(server))
    good = hashlib.md5(body).hexdigest()
    path = download(tmp_path, client=client, url=URL, expected_md5=good)
    assert ranges == [f"bytes={len(too_long)}-", None]
    assert path.read_bytes() == body


def test_download_keeps_a_part_file_that_already_holds_the_archive(tmp_path):
    # An earlier run saved every byte but stopped before the check finished.
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    part = _part_file(tmp_path, body)
    ranges = []

    def server(request):
        ranges.append(request.headers.get("range"))
        if "range" in request.headers:
            return httpx.Response(416)
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(server))
    good = hashlib.md5(body).hexdigest()
    path = download(tmp_path, client=client, url=URL, expected_md5=good)
    assert ranges == [f"bytes={len(body)}-"]
    assert path.read_bytes() == body
    assert not part.exists()


def test_a_resumed_download_that_fails_the_check_leaves_no_part_file(tmp_path):
    body = _tgz({"FantasyID/readme.txt": b"hello"})
    part = _part_file(tmp_path, b"not the start of the archive")
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(206, content=body[_first_byte(r) :]))
    )
    good = hashlib.md5(body).hexdigest()
    with pytest.raises(ChecksumError):
        download(tmp_path, client=client, url=URL, expected_md5=good)
    assert not part.exists()
    assert not (tmp_path / "raw" / "FantasyID.tgz").exists()


def test_notice_names_the_folder_the_data_goes_into():
    assert "Kept in datasets/ on your machine, never committed." in notice(Path("datasets"))


def test_extract_unpacks_normal_members(tmp_path):
    archive = tmp_path / "a.tgz"
    archive.write_bytes(_tgz({"FantasyID/x/card.jpg": b"jpg"}))
    out = extract(archive, tmp_path / "out")
    assert (out / "FantasyID" / "x" / "card.jpg").read_bytes() == b"jpg"
    assert (out / ".gitignore").read_text() == "*\n"


def test_extract_refuses_paths_outside_the_target(tmp_path):
    archive = tmp_path / "evil.tgz"
    archive.write_bytes(_tgz({"../escape.txt": b"nope"}))
    with pytest.raises(tarfile.FilterError):
        extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize(
    ("kind", "linkname"),
    [
        (tarfile.SYMTYPE, "../../x"),
        (tarfile.LNKTYPE, "../x"),
        (tarfile.SYMTYPE, "/etc/passwd"),
        (tarfile.CHRTYPE, ""),
    ],
    ids=["symlink_escape", "hardlink_escape", "absolute_symlink", "char_device"],
)
def test_extract_refuses_unsafe_links_and_devices(tmp_path, kind, linkname):
    info = tarfile.TarInfo("FantasyID/bad")
    info.type = kind
    info.linkname = linkname
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.addfile(info)
    archive = tmp_path / "evil.tgz"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(tarfile.FilterError):
        extract(archive, tmp_path / "out")


@pytest.mark.parametrize(
    ("folder", "command"),
    [(None, "kyc download"), ("my data", 'kyc --data-dir "my data" download')],
    ids=["default_folder", "chosen_folder"],
)
def test_cli_extract_without_an_archive_says_where_it_looked(
    tmp_path, monkeypatch, capsys, folder, command
):
    monkeypatch.chdir(tmp_path)
    given = ["--data-dir", folder] if folder else []
    assert main([*given, "extract"]) == 1
    err = capsys.readouterr().err
    assert f"No archive at {folder or 'data'}/raw/FantasyID.tgz." in err
    assert f"Run `{command}` first." in err


def test_cli_extract_refuses_an_archive_with_the_wrong_md5(tmp_path, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "FantasyID.tgz").write_bytes(_tgz({"FantasyID/card.jpg": b"jpg"}))
    assert main(["--data-dir", str(tmp_path), "extract"]) == 1
    assert not (tmp_path / "fantasyid").exists()
    err = capsys.readouterr().err
    assert f'Run `kyc --data-dir "{tmp_path.as_posix()}" download` to fetch it again.' in err


def test_cli_extract_unpacks_an_archive_with_the_published_md5(tmp_path, monkeypatch):
    body = _tgz({"FantasyID/card.jpg": b"jpg"})
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "FantasyID.tgz").write_bytes(body)
    monkeypatch.setattr("kyc.cli.FANTASYID_MD5", hashlib.md5(body).hexdigest())
    assert main(["--data-dir", str(tmp_path), "extract"]) == 0
    assert (tmp_path / "fantasyid" / "FantasyID" / "card.jpg").read_bytes() == b"jpg"


def test_cli_download_asks_first_and_names_the_data_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("kyc.cli.download", _no_download)
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a terminal, so no prompt
    assert main(["--data-dir", str(tmp_path), "download"]) == 2
    out = capsys.readouterr().out
    assert f"Kept in {tmp_path.as_posix()}/ on your machine" in out
    assert "Not downloaded. Run again with --yes to accept the notice." in out


def test_cli_download_takes_end_of_input_at_the_prompt_as_no(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("kyc.cli.download", _no_download)
    monkeypatch.setattr(sys, "stdin", _Terminal())  # a terminal with nothing left to read
    assert main(["--data-dir", str(tmp_path), "download"]) == 2
    assert "Not downloaded. Run again with --yes to accept the notice." in capsys.readouterr().out


def test_cli_download_ends_the_progress_line_before_saying_what_went_wrong(tmp_path, monkeypatch):
    def failing_download(data_dir, *, on_progress=None):
        on_progress(1_300_000_000)
        raise ChecksumError("bad archive")

    monkeypatch.setattr("kyc.cli.download", failing_download)
    terminal = _Terminal()
    monkeypatch.setattr(sys, "stderr", terminal)
    assert main(["--data-dir", str(tmp_path), "download", "--yes"]) == 1
    assert terminal.getvalue().startswith(
        "\r1.30 of about 2.6 GB\nbad archive. The downloaded file was deleted."
    )


def test_cli_download_says_how_to_pick_up_after_a_dropped_connection(tmp_path, monkeypatch, capsys):
    def dropped_download(data_dir, *, on_progress=None):
        raise httpx.ReadError("connection dropped")

    monkeypatch.setattr("kyc.cli.download", dropped_download)
    assert main(["--data-dir", str(tmp_path), "download", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "Download stopped: connection dropped" in err
    assert "Run the same command again to pick up where it stopped." in err


def test_cli_download_shows_no_progress_when_stderr_is_redirected(tmp_path, monkeypatch):
    given = []

    def quiet_download(data_dir, *, on_progress=None):
        given.append(on_progress)
        return data_dir / "raw" / "FantasyID.tgz"

    monkeypatch.setattr("kyc.cli.download", quiet_download)
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    assert main(["--data-dir", str(tmp_path), "download", "--yes"]) == 0
    assert given == [None]
    assert stderr.getvalue() == ""
    assert f"Verified {tmp_path.as_posix()}/raw/FantasyID.tgz" in stdout.getvalue()


def test_unpacking_needs_a_python_with_the_tarfile_fixes():
    from kyc.data import has_tarfile_fixes

    assert has_tarfile_fixes((3, 12, 11)) and has_tarfile_fixes((3, 13, 4))
    assert not has_tarfile_fixes((3, 12, 10)) and not has_tarfile_fixes((3, 13, 3))
    assert has_tarfile_fixes()  # the locked environment itself
