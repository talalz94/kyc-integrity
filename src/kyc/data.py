"""Download and unpack FantasyID from Zenodo, safely.

Only the open part of FantasyID (Idiap, IJCB 2025) is fetched: Zenodo record 17063366, which is
CC BY 4.0. The FantasyID-val part (record 17063494) is restricted to noncommercial research under
its own license agreement and is not fetched. The archive is checked against the MD5 that Zenodo
publishes, then unpacked with Python's "data" extraction filter, which keeps every member inside
the target and refuses links that point outside it, device files and other unsafe members. Use
Python 3.12.11, 3.13.4 or a later release: they fixed known ways around that filter.
"""

from __future__ import annotations

import hashlib
import platform
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path

import httpx

FANTASYID_RECORD = "17063366"
FANTASYID_FILE = "FantasyID.tgz"
FANTASYID_URL = f"https://zenodo.org/records/{FANTASYID_RECORD}/files/{FANTASYID_FILE}?download=1"
FANTASYID_MD5 = "5a6e608762f640db026ac122b4400a72"  # as published on the Zenodo record


def notice(data_dir: Path) -> str:
    """The notice shown before downloading, naming the folder the data will go into."""
    return f"""\
FantasyID data notice
=====================
Source:  https://zenodo.org/records/{FANTASYID_RECORD} (Idiap Research Institute)
Paper:   Korshunov et al., "FantasyID: A dataset for detecting digital manipulations of
         ID-documents", IJCB 2025, arXiv:2507.20808
License: CC BY 4.0. If you share the images or edited versions, credit the authors, link
         the license and say if you changed them. FantasyID-val (Zenodo 17063494) is
         restricted and not fetched.
Request: the cards show faces of real people. Please use the images for research and do
         not republish them out of context.
Size:    about 2.6 GB. Kept in {data_dir.as_posix()}/ on your machine, never committed.
"""


class ChecksumError(RuntimeError):
    pass


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _keep_out_of_git(folder: Path) -> None:
    """Create folder with a .gitignore that ignores everything in it, wherever it lives."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gitignore").write_text("*\n", encoding="utf-8")


def download(
    data_dir: Path,
    *,
    client: httpx.Client | None = None,
    url: str = FANTASYID_URL,
    expected_md5: str = FANTASYID_MD5,
    on_progress: Callable[[int], None] | None = None,
) -> Path:
    """Fetch the archive into data_dir/raw and check it against the published MD5.

    An archive that is already there and passes the check is kept as it is. If an earlier run
    stopped partway, the bytes it saved in FantasyID.tgz.part are kept and only the rest is
    requested. A part file that already holds the whole archive is checked and kept. Otherwise,
    when the server cannot send just the rest, the file starts over. A download that fails the
    check is deleted, so the next run starts clean. on_progress gets the number of bytes saved
    so far.
    """
    dest = data_dir / "raw" / FANTASYID_FILE
    _keep_out_of_git(dest.parent)
    if dest.exists() and md5(dest) == expected_md5:
        return dest
    partial = dest.with_name(dest.name + ".part")
    own = client is None
    http = client or httpx.Client(timeout=600.0, follow_redirects=True)
    try:
        if not _fetch(http, url, partial, on_progress) and md5(partial) != expected_md5:
            # The server has nothing after the bytes already saved, and they are not the
            # archive (a complete one is kept), so start over from the first byte.
            partial.unlink()
            _fetch(http, url, partial, on_progress)
    finally:
        if own:
            http.close()
    actual = md5(partial)
    if actual != expected_md5:
        partial.unlink(missing_ok=True)
        raise ChecksumError(f"{FANTASYID_FILE}: expected md5 {expected_md5}, got {actual}")
    partial.replace(dest)
    return dest


def _fetch(
    http: httpx.Client,
    url: str,
    partial: Path,
    on_progress: Callable[[int], None] | None,
) -> bool:
    """Stream url into partial, asking only for the bytes it does not have yet.

    New bytes go on the end of partial only when the server answers 206 Partial Content. Any
    other successful answer carries the whole file, so partial is written again from the
    start. Returns False, and leaves partial alone, when the server answers 416 because
    partial already holds as many bytes as the file or more.
    """
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else None
    with http.stream("GET", url, headers=headers) as response:
        if have and response.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE:
            return False
        response.raise_for_status()
        resume = have > 0 and response.status_code == httpx.codes.PARTIAL_CONTENT
        done = have if resume else 0
        with partial.open("ab" if resume else "wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done)
    return True


def has_tarfile_fixes(version: tuple[int, ...] = tuple(sys.version_info[:3])) -> bool:
    """Python's "data" filter had ways around it until 3.12.11 and 3.13.4 (June 2025)."""
    return version >= (3, 13, 4) or (3, 12, 11) <= version < (3, 13)


def extract(archive: Path, target: Path) -> Path:
    """Unpack with the "data" filter: no absolute paths, no escaping links, no devices."""
    if not has_tarfile_fixes():
        raise RuntimeError(
            f"Python {platform.python_version()} has known gaps in tarfile's safety filter. "
            "Use Python 3.12.11 or later (or 3.13.4 or later) to unpack the archive."
        )
    _keep_out_of_git(target)
    with tarfile.open(archive, "r:*") as tar:
        tar.extractall(target, filter="data")
    return target
