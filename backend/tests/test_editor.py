from drive_sync.editor import Editor, find_editor


def _which_for(available: dict[str, str]):
    return lambda name: available.get(name)


def test_vscode_wins_when_its_cli_is_on_path():
    which = _which_for({"code": "/opt/homebrew/bin/code", "nvim": "/opt/homebrew/bin/nvim"})
    assert find_editor({"EDITOR": "nvim"}, which) == Editor(["/opt/homebrew/bin/code"], gui=True)


def test_users_editor_beats_the_fallbacks_and_keeps_its_arguments():
    which = _which_for({"emacs": "/usr/bin/emacs", "nvim": "/opt/homebrew/bin/nvim", "vim": "/usr/bin/vim"})
    assert find_editor({"EDITOR": "emacs -nw"}, which) == Editor(["/usr/bin/emacs", "-nw"], gui=False)


def test_visual_beats_editor():
    which = _which_for({"hx": "/usr/bin/hx", "nano": "/usr/bin/nano"})
    assert find_editor({"VISUAL": "hx", "EDITOR": "nano"}, which) == Editor(["/usr/bin/hx"], gui=False)


def test_an_editor_that_is_not_installed_is_skipped():
    which = _which_for({"vim": "/usr/bin/vim"})
    assert find_editor({"EDITOR": "subl -w"}, which) == Editor(["/usr/bin/vim"], gui=False)


def test_nvim_before_vim_then_nothing():
    assert find_editor({}, _which_for({"nvim": "/n", "vim": "/v"})) == Editor(["/n"], gui=False)
    assert find_editor({}, _which_for({"vim": "/v"})) == Editor(["/v"], gui=False)
    assert find_editor({}, _which_for({})) is None
