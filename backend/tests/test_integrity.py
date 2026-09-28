"""The verdict comes from ffmpeg's stderr, so the parsing is what gets tested.

The stderr samples are verbatim from a scan of Let's Play S01E08 on the
household server, the episode that sent Plex on to the next one mid-play.
"""

from drive_sync.integrity import FileReport, parse_damage, part_url, scan_part

# One 1,487,641,472 byte file, 1375.125 s long.
SIZE = 1487641472
DURATION_MS = 1375125

DAMAGED_STDERR = (
    "\n".join(
        [
            "[matroska,webm @ 0x1493f64a3380] Length 6 indicated by an EBML number's first byte"
            " 0x06 at pos 508947958 (0x1e55edf6) exceeds max length 4.",
            "[matroska,webm @ 0x1493f64a3380] Element at 0x1e612c12 ending at 0x1e741b74"
            " exceeds containing master element ending at 0x1e6806ff",
            "[matroska,webm @ 0x1493f64a3380] 0x00 at pos 780620059 (0x2e87511b) invalid as first"
            " byte of an EBML number",
            "[matroska,webm @ 0x1493f64a3380] Element at 0x4cb0ea91 ending at 0x56b425d5"
            " exceeds containing master element ending at 0x4cb5d5e8",
        ]
    )
    + "\n"
)


def test_a_clean_file_prints_nothing_and_reads_as_clean():
    assert parse_damage("", SIZE, DURATION_MS) == []
    assert FileReport("S01E01", "/data/x.mkv", SIZE, DURATION_MS).is_clean


def test_every_stderr_line_is_a_finding():
    damage = parse_damage(DAMAGED_STDERR, SIZE, DURATION_MS)
    assert len(damage) == 4
    assert not FileReport("S01E08", "/data/x.mkv", SIZE, DURATION_MS, damage=damage).is_clean


def test_the_demuxer_prefix_is_dropped_from_the_message():
    first = parse_damage(DAMAGED_STDERR, SIZE, DURATION_MS)[0]
    assert first.message.startswith("Length 6 indicated by")


def test_an_offset_is_read_decimal_from_pos_and_hex_from_element_at():
    damage = parse_damage(DAMAGED_STDERR, SIZE, DURATION_MS)
    assert damage[0].offset == 508947958
    assert damage[1].offset == 0x1E612C12
    assert damage[2].offset == 780620059
    assert damage[3].offset == 0x4CB0EA91


def test_an_offset_becomes_a_place_in_the_runtime():
    # 508,947,958 of 1,487,641,472 bytes into a 22:55 episode is about 7:50.
    first = parse_damage(DAMAGED_STDERR, SIZE, DURATION_MS)[0]
    assert first.at_seconds is not None
    assert abs(first.at_seconds - 470.5) < 1
    assert first.at_clock == "07:50"


def test_a_line_naming_no_offset_still_counts():
    damage = parse_damage("[matroska,webm @ 0x14] Invalid track number 14671\n", SIZE, DURATION_MS)
    assert len(damage) == 1
    assert damage[0].offset is None
    assert damage[0].at_clock == "--:--"


def test_an_unsized_part_places_nothing_but_still_reports():
    damage = parse_damage(DAMAGED_STDERR, 0, 0)
    assert len(damage) == 4
    assert all(finding.at_seconds is None for finding in damage)


def test_the_part_url_is_the_server_plus_the_part_key(monkeypatch):
    monkeypatch.setenv("PLEX_SERVER", "http://192.168.0.2:32400/")
    assert part_url({"key": "/library/parts/288971/1787843870/file.mkv"}) == (
        "http://192.168.0.2:32400/library/parts/288971/1787843870/file.mkv"
    )


def test_a_missing_ffmpeg_is_reported_against_the_part_not_raised(monkeypatch):
    monkeypatch.setattr("drive_sync.integrity.FFMPEG", "ffmpeg-that-is-not-installed")
    monkeypatch.setenv("PLEX_SERVER", "http://192.168.0.2:32400")
    monkeypatch.setenv("PLEX_TOKEN", "xxx")
    report = scan_part("S01E01", {"key": "/library/parts/1/2/file.mkv", "size": SIZE, "duration": DURATION_MS})
    assert report.verdict == "UNREADABLE"
    assert "not installed" in report.unreadable
