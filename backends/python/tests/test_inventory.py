import json
from pathlib import Path
from textwrap import dedent

from engine.inventory import find_machine, machines_to_json, parse_ansible_ini, parse_inventory


def _write_hosts_json(tmp_path: Path) -> Path:
    inv = tmp_path / "hosts.json"
    inv.write_text(
        json.dumps(
            {
                "hosts": [
                    {
                        "name": "behemoth",
                        "hostname": "192.168.86.31",
                        "user": "root",
                        "os": "linux",
                        "harness": "ssh",
                        "groups": ["unraid"],
                        "aliases": ["sshbehemoth"],
                        "services": [
                            {
                                "type": "sonarr",
                                "name": "sonarr-behemoth",
                                "port": 8989,
                                "api_key_env": "SONARR_BEHEMOTH_API_KEY",
                            }
                        ],
                    },
                    {"name": "AppleTV", "os": "ios", "harness": "none", "groups": ["iOS"]},
                ]
            }
        )
    )
    return inv


def test_parse_hosts_json(tmp_path):
    machines = parse_inventory(_write_hosts_json(tmp_path))
    assert len(machines) == 2

    behemoth = machines[0]
    assert behemoth.id == "behemoth"
    assert behemoth.hostname == "192.168.86.31"
    assert behemoth.user == "root"
    assert behemoth.port == 22
    assert behemoth.os == "linux"
    assert behemoth.harness == "ssh"
    assert behemoth.groups == ["unraid"]
    assert behemoth.aliases == ["sshbehemoth"]
    assert len(behemoth.services) == 1
    assert behemoth.services[0].type == "sonarr"
    assert behemoth.services[0].port == 8989
    assert behemoth.services[0].api_key_env == "SONARR_BEHEMOTH_API_KEY"

    appletv = machines[1]
    assert appletv.harness == "none"
    assert appletv.hostname == "AppleTV"  # defaults to name
    assert appletv.services == []


def test_find_machine_matches_id_name_alias(tmp_path):
    machines = parse_inventory(_write_hosts_json(tmp_path))
    assert find_machine(machines, "behemoth")[0].id == "behemoth"
    assert find_machine(machines, "BEHEMOTH")[0].id == "behemoth"
    assert find_machine(machines, "sshbehemoth")[0].id == "behemoth"
    assert find_machine(machines, "nope") == []


def test_parse_real_inventory():
    """The repo-root hosts.json must parse and contain the media hosts."""
    machines = parse_inventory()
    assert len(machines) > 0
    by_name = {m.name: m for m in machines}
    assert "behemoth" in by_name
    assert any(s.type == "sonarr" for s in by_name["behemoth"].services)


def test_ansible_ini_roundtrip(tmp_path):
    ini = tmp_path / "hosts"
    ini.write_text(
        dedent("""\
        # comment
        [macs]
        MacBookPro12 ansible_user=jason ssh_alias=sshmac

        [macs:vars]
        ansible_user=jason

        [raspbian]
        raspberrypi4 ssh_alias=sshpi4

        [raspbian:vars]
        ansible_user=pi

        [networking]
        asusrouter ansible_host=192.168.86.1

        [game_consoles]
        Switch
    """)
    )
    machines = parse_ansible_ini(ini)
    by_name = {m.name: m for m in machines}

    assert by_name["MacBookPro12"].user == "jason"
    assert by_name["MacBookPro12"].os == "macos"
    assert by_name["MacBookPro12"].aliases == ["sshmac"]
    assert by_name["MacBookPro12"].harness == "ssh"

    # group vars supply the user
    assert by_name["raspberrypi4"].user == "pi"
    assert by_name["raspberrypi4"].harness == "ssh"

    # no user but explicit IP -> ping
    assert by_name["asusrouter"].harness == "ping"
    assert by_name["asusrouter"].hostname == "192.168.86.1"

    # nothing to connect to -> none
    assert by_name["Switch"].harness == "none"

    # round-trips through the JSON format
    out = tmp_path / "hosts.json"
    out.write_text(machines_to_json(machines))
    reparsed = parse_inventory(out)
    assert {m.name for m in reparsed} == {m.name for m in machines}
