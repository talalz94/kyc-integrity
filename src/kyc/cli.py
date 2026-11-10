"""Command line entry point.

kyc download    fetch FantasyID from Zenodo (asks you to accept its notice), check its MD5;
                if it stops partway, run it again to pick up where it stopped
kyc extract     check the MD5 again, then unpack it safely into <data dir>/fantasyid
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from kyc.data import FANTASYID_FILE, FANTASYID_MD5, ChecksumError, download, extract, md5, notice

DATA_DIR = Path("data")


def _accepted() -> bool:
    """Ask on a terminal whether to download. No terminal, or end of input, counts as no."""
    if not sys.stdin.isatty():
        return False
    try:
        return input("Type yes to download: ").strip().lower() == "yes"
    except EOFError:  # Ctrl+D or Ctrl+Z, or input from NUL, which Windows reports as a terminal
        print()
        return False


def _download_with_progress(data_dir: Path) -> Path:
    """Download, showing how much has arrived when stderr is a terminal."""
    if not sys.stderr.isatty():
        return download(data_dir)

    def progress(done: int) -> None:
        print(f"\r{done / 1e9:.2f} of about 2.6 GB", end="", file=sys.stderr, flush=True)

    try:
        return download(data_dir, on_progress=progress)
    finally:
        print(file=sys.stderr)  # end the progress line, even when the download fails


def _download_command(data_dir: Path) -> str:
    """The download command for data_dir, so advice to run it also works with --data-dir."""
    if data_dir == DATA_DIR:
        return "kyc download"
    return f'kyc --data-dir "{data_dir.as_posix()}" download'


def cmd_download(args) -> int:
    print(notice(args.data_dir))
    if not args.yes and not _accepted():
        print("Not downloaded. Run again with --yes to accept the notice.")
        return 2
    try:
        path = _download_with_progress(args.data_dir)
    except ChecksumError as err:
        print(
            f"{err}. The downloaded file was deleted. Run the same command to fetch it again.",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as err:
        print(
            f"Download stopped: {err}\nRun the same command again to pick up where it stopped.",
            file=sys.stderr,
        )
        return 1
    print(f"Verified {path.as_posix()}")
    return 0


def cmd_extract(args) -> int:
    archive = args.data_dir / "raw" / FANTASYID_FILE
    command = _download_command(args.data_dir)
    if not archive.exists():
        print(f"No archive at {archive.as_posix()}. Run `{command}` first.", file=sys.stderr)
        return 1
    if md5(archive) != FANTASYID_MD5:
        print(
            f"{archive.as_posix()} does not match the MD5 Zenodo publishes. "
            f"Run `{command}` to fetch it again.",
            file=sys.stderr,
        )
        return 1
    out = extract(archive, args.data_dir / "fantasyid")
    print(f"Unpacked into {out.as_posix()}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kyc", description="KYC integrity.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("download", help="fetch FantasyID from Zenodo (run again to resume)")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_download)
    p = sub.add_parser("extract", help="check the MD5, then unpack FantasyID safely")
    p.set_defaults(func=cmd_extract)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
