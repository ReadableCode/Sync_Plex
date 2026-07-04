import asyncio
import json

from engine.sync_jobs import DEFAULT_CONFIG_PATH, SyncJob, load_sync_jobs, render_path

SAMPLE_CONFIG = {
    "sync_folders": [
        {
            "sync_name": "Audiobooks",
            "src_path_type": "Windows Network Mount",
            "src_path": ["192.168.86.31", "Media", "Audiobooks"],
            "dest_path_type": "Windows Local Drive",
            "dest_path": ["I:", "Media", "Audiobooks"],
            "included_subfolders": [
                ["Orson Scott Card", "Enderverse- Publication Order"],
                ["Sarah J. Maas", "Throne of Glass"],
            ],
            "included_files": [["Standalone", "book.m4b"]],
        }
    ]
}


def test_load_sync_jobs_round_trip(tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps(SAMPLE_CONFIG, indent=4))

    jobs = load_sync_jobs(config_path)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.sync_name == "Audiobooks"
    assert job.src_path_type == "Windows Network Mount"
    assert job.src_path == ["192.168.86.31", "Media", "Audiobooks"]
    assert job.dest_path_type == "Windows Local Drive"
    assert job.dest_path == ["I:", "Media", "Audiobooks"]
    assert job.included_subfolders == [
        ["Orson Scott Card", "Enderverse- Publication Order"],
        ["Sarah J. Maas", "Throne of Glass"],
    ]
    assert job.included_files == [["Standalone", "book.m4b"]]


def test_load_sync_jobs_tolerates_missing_optional_keys(tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(
        json.dumps({"sync_folders": [{"sync_name": "Bare", "src_path": ["a"], "dest_path": ["b"]}]})
    )

    (job,) = load_sync_jobs(config_path)
    assert job.sync_name == "Bare"
    assert job.included_subfolders == []
    assert job.included_files == []


def test_load_sync_jobs_missing_file_returns_empty(tmp_path):
    assert load_sync_jobs(tmp_path / "does_not_exist.json") == []


def test_load_sync_jobs_invalid_json_returns_empty(tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text("{not json")
    assert load_sync_jobs(config_path) == []


def test_load_sync_jobs_wrong_shape_returns_empty(tmp_path):
    config_path = tmp_path / "sync_config.json"
    config_path.write_text(json.dumps(["not", "a", "dict"]))
    assert load_sync_jobs(config_path) == []


def test_default_path_is_repo_root_sync_config():
    assert DEFAULT_CONFIG_PATH.name == "sync_config.json"
    # engine/config.py REPO_ROOT resolves backends/python/engine three levels up
    assert (DEFAULT_CONFIG_PATH.parent / "backends" / "python" / "engine").is_dir()


def test_render_path_network_mount():
    assert (
        render_path("Windows Network Mount", ["192.168.86.31", "Media", "Audiobooks"])
        == "\\\\192.168.86.31\\Media\\Audiobooks"
    )


def test_render_path_local_drive():
    assert render_path("Windows Local Drive", ["I:", "Media", "Audiobooks"]) == "I:\\Media\\Audiobooks"


def test_render_path_unknown_type_backslash_join():
    assert render_path("Something Else", ["a", "b"]) == "a\\b"


def test_sync_job_display_properties():
    job = SyncJob(
        sync_name="Audiobooks",
        src_path_type="Windows Network Mount",
        src_path=["192.168.86.31", "Media", "Audiobooks"],
        dest_path_type="Windows Local Drive",
        dest_path=["I:", "Media", "Audiobooks"],
    )
    assert job.src_display == "\\\\192.168.86.31\\Media\\Audiobooks"
    assert job.dest_display == "I:\\Media\\Audiobooks"


def test_tui_sync_screen_lists_jobs(monkeypatch):
    """The sync screen mounts from the media app and shows configured jobs."""
    import engine.media.tui.app as tui_app
    from engine.media.config import MediaConfig

    job = SyncJob(
        sync_name="Audiobooks",
        src_path_type="Windows Network Mount",
        src_path=["192.168.86.31", "Media", "Audiobooks"],
        dest_path_type="Windows Local Drive",
        dest_path=["I:", "Media", "Audiobooks"],
        included_subfolders=[["Sarah J. Maas", "Throne of Glass"]],
    )
    monkeypatch.setattr(tui_app, "load_media_config", lambda: MediaConfig())
    monkeypatch.setattr(tui_app, "load_sync_jobs", lambda: [job])

    async def _run():
        app = tui_app.MediaRemote()
        async with app.run_test() as pilot:
            await pilot.press("ctrl+s")
            assert isinstance(app.screen, tui_app.SyncScreen)
            tree = app.screen.query_one(tui_app.Tree)
            labels = [str(node.label) for node in tree.root.children]
            assert any("Audiobooks" in label for label in labels)
            assert any("\\\\192.168.86.31\\Media\\Audiobooks" in label for label in labels)
            leaf_labels = [str(leaf.label) for leaf in tree.root.children[0].children]
            assert "Sarah J. Maas\\Throne of Glass" in leaf_labels
            await pilot.press("escape")
            assert app.screen is app.screen_stack[0]

    asyncio.run(_run())
