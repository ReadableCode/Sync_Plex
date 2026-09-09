"""`syncplex-drive-sync [folder] [--check]`: the TUI by default, a headless
plan for scripts and cmdr's check convention with --check."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from drive_sync.drive_config import ConfigError, DriveConfig
from drive_sync.library import PlexLibrary
from drive_sync.plan import make_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror a drive config's shows and movies from Plex onto the drive.")
    parser.add_argument("folder", nargs="?", help="the drive's media folder; omit it to browse for one")
    parser.add_argument(
        "--check",
        action="store_true",
        help="no TUI: print what a sync would do and exit 1 when the drive differs from its config",
    )
    args = parser.parse_args()
    if args.check:
        sys.exit(check(Path(args.folder) if args.folder else None))
    from drive_sync.app import DriveSyncApp

    DriveSyncApp(Path(args.folder).resolve() if args.folder else None).run()


def check(folder: Path | None) -> int:
    if folder is None:
        print("--check needs the drive's media folder", file=sys.stderr)
        return 2
    try:
        config = DriveConfig.load(folder)
    except ConfigError as exc:
        print(f"config: {exc}")
        return 1
    plan = make_plan(config, PlexLibrary(), folder)
    for miss in plan.missing:
        hint = f" (did you mean {miss.suggestions[0]}?)" if miss.suggestions else ""
        print(f"not on plex: {miss.kind} {miss.name}{hint}")
    for action in plan.actions_needed:
        print(f"{action.op:<8} {action.size_gb:6.2f} GB  {action.title}  {action.label}")
    free = "unknown" if plan.free_gb is None else f"{plan.free_gb:.2f} GB"
    print(
        f"get {len(plan.downloads)} ({plan.download_gb:.2f} GB), remove {len(plan.deletes)} "
        f"({plan.delete_gb:.2f} GB), net {plan.net_gb:.2f} GB, free {free}"
    )
    if plan.is_clean:
        print("drive matches its config")
        return 0
    if plan.fits is False:
        print("the downloads do not fit on the drive")
    return 1


if __name__ == "__main__":
    main()
