"""Does a Plex file actually play all the way through?

The failure this exists for: a file whose matroska cluster chain is damaged
somewhere in the middle. The demuxer loses sync there, the transcode stops
early, and the client reads that as the end of the episode and rolls on to the
next one. Nothing in the library view gives it away - duration, size, bitrate
and the thumbnails all read normal, because they come from the header and the
header is intact.

The read goes through Plex's own part URL, so what is checked is the byte
stream a player receives, from any machine that has the token. ffmpeg demuxes
without decoding, so the cost is the download and not the CPU.

    python -m drive_sync.integrity "Let's Play"
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field

from drive_sync import plex_api_wrapper as plex
from drive_sync.library import PlexLibrary, fuzzy_find

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

# A 1080p episode is a couple of GB over the LAN; a movie is several.
DEFAULT_TIMEOUT = 60 * 60.0

# ffmpeg exits 0 on a file it demuxed with damage in the middle: it prints the
# damage and carries on to the end. So the verdict is stderr, never the exit
# code. At -v error a healthy file prints nothing at all, which is why any line
# here counts as a finding rather than a list of messages known to be bad.
_OFFSETS = (
    re.compile(r"\bat pos (\d+)\b"),  # "0x00 at pos 1053024863 (0x3ec3e25f) invalid as ..."
    re.compile(r"\bElement at 0x([0-9a-f]+)", re.I),  # "Element at 0x3249cfe5 ending at ..."
    re.compile(r"\boffset 0x([0-9a-f]+)", re.I),  # mp4's "offset 0x...: partial file"
)
_PREFIX = re.compile(r"^\[[^\]]+ @ 0x[0-9a-f]+\]\s*", re.I)  # "[matroska,webm @ 0x14..] "


@dataclass(frozen=True)
class Damage:
    """One line ffmpeg complained about, placed in the file and in the runtime."""

    message: str
    offset: int | None  # byte offset the line names, when it names one
    at_seconds: float | None  # that offset mapped onto the runtime, approximate

    @property
    def at_clock(self) -> str:
        if self.at_seconds is None:
            return "--:--"
        return f"{int(self.at_seconds) // 60:02d}:{int(self.at_seconds) % 60:02d}"


@dataclass
class FileReport:
    """One media part, scanned end to end."""

    label: str  # "S01E08  Unalive"
    file: str  # the path on the server, for finding the file once it is condemned
    size: int  # bytes, as Plex records them
    duration_ms: int
    damage: list[Damage] = field(default_factory=list)
    unreadable: str = ""  # ffmpeg could not read the part at all

    @property
    def is_clean(self) -> bool:
        return not self.damage and not self.unreadable

    @property
    def verdict(self) -> str:
        if self.unreadable:
            return "UNREADABLE"
        return "ok" if not self.damage else "DAMAGED"

    def summary(self) -> str:
        if self.unreadable:
            return f"{self.label}  {self.verdict}  {self.unreadable}"
        if not self.damage:
            return f"{self.label}  {self.verdict}"
        first = self.damage[0]
        return f"{self.label}  {self.verdict}  {len(self.damage)} findings, first around {first.at_clock}"


def part_url(part: dict) -> str:
    """The Plex URL that serves a part's bytes ("/library/parts/<id>/<ts>/file.mkv")."""
    return plex.get_plex_server().rstrip("/") + part["key"]


def _offset_in(message: str) -> int | None:
    for index, pattern in enumerate(_OFFSETS):
        match = pattern.search(message)
        if match:
            return int(match.group(1), 10 if index == 0 else 16)
    return None


def parse_damage(stderr: str, size: int, duration_ms: int) -> list[Damage]:
    """Every stderr line ffmpeg produced, with its byte offset read back as a runtime.

    The runtime is offset/size scaled over the duration: near enough to seek to,
    exact only for constant-bitrate files.
    """
    damage = []
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line:
            continue
        message = _PREFIX.sub("", line)
        offset = _offset_in(message)
        at_seconds = None
        if offset is not None and size > 0 and duration_ms > 0:
            at_seconds = offset / size * duration_ms / 1000
        damage.append(Damage(message=message, offset=offset, at_seconds=at_seconds))
    return damage


def scan_part(label: str, part: dict, timeout: float = DEFAULT_TIMEOUT) -> FileReport:
    """Read one part end to end through Plex and report what ffmpeg saw."""
    size = int(part.get("size") or 0)
    duration_ms = int(part.get("duration") or 0)
    report = FileReport(label=label, file=part.get("file", ""), size=size, duration_ms=duration_ms)
    command = [
        FFMPEG,
        "-nostdin",
        "-v",
        "error",
        # The token goes in a header, not the query string, so it stays out of
        # the URL Plex writes to its own access log.
        "-headers",
        f"X-Plex-Token: {plex.get_plex_token()}\r\n",
        "-i",
        part_url(part),
        # Demux only: -c copy never decodes, so this is bound by the download.
        "-map",
        "0:v",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        report.unreadable = f"{FFMPEG} is not installed"
        return report
    except subprocess.TimeoutExpired:
        report.unreadable = f"no answer in {timeout:.0f}s"
        return report
    report.damage = parse_damage(result.stderr, size, duration_ms)
    if result.returncode != 0:
        # A non-zero exit means ffmpeg gave up rather than read past the damage:
        # the part is not merely damaged, it is unplayable from here.
        last = report.damage[-1].message if report.damage else f"ffmpeg exited {result.returncode}"
        report.unreadable = last
    return report


def show_parts(name: str, library: PlexLibrary | None = None) -> Iterator[tuple[str, dict]]:
    """(label, part) for every episode of the show whose title best matches `name`."""
    library = library or PlexLibrary()
    matches = fuzzy_find(name, library.shows(), limit=1)
    if not matches:
        raise LookupError(f"no show on Plex matching {name!r}")
    show = matches[0]
    for _, episode in library.episodes(show):
        season = episode.get("parentIndex", 0)
        index = episode.get("index", 0)
        for media in episode.get("Media", []):
            for part in media.get("Part", []):
                yield f"S{season:02d}E{index:02d}  {episode.get('title', '')}", part


def scan_show(name: str, library: PlexLibrary | None = None, timeout: float = DEFAULT_TIMEOUT) -> list[FileReport]:
    """Every episode of a show, read end to end. Minutes per episode over the LAN."""
    return [scan_part(label, part, timeout) for label, part in show_parts(name, library)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('usage: python -m drive_sync.integrity "<show title>"', file=sys.stderr)
        return 2
    name = args[0]
    bad = 0
    for label, part in show_parts(name):
        report = scan_part(label, part)
        print(report.summary(), flush=True)
        for finding in report.damage:
            print(f"    {finding.at_clock}  {finding.message}", flush=True)
        if not report.is_clean:
            bad += 1
    print(f"\n{bad} damaged" if bad else "\nall clean")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
