"""Which editor opens a drive's config.yaml: VS Code when its `code` command is
on PATH, else the user's own $VISUAL / $EDITOR, else nvim, else vim."""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Editor:
    command: list[str]
    gui: bool  # a window: launch and return; a terminal editor suspends the picker until it exits


def find_editor(environ: dict[str, str] | None = None, which=shutil.which) -> Editor | None:
    """VS Code when its `code` command is on PATH, else the user's own $VISUAL /
    $EDITOR (split like a shell would, so "code --wait" or "emacs -nw" work),
    else nvim, else vim. None when nothing is there."""
    environ = os.environ if environ is None else environ
    if which("code"):
        return Editor([which("code")], gui=True)
    for var in ("VISUAL", "EDITOR"):
        words = shlex.split(environ.get(var, ""))
        if words and which(words[0]):
            return Editor([which(words[0]), *words[1:]], gui=False)
    for name in ("nvim", "vim"):
        if which(name):
            return Editor([which(name)], gui=False)
    return None
