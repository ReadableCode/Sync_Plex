import json
import re
from pathlib import Path

from .config import get_inventory_path
from .models import Machine, Service


def parse_inventory(path: Path | None = None) -> list[Machine]:
    """Load the hosts.json inventory into a list of Machine objects."""
    if path is None:
        path = get_inventory_path()
    if path is None or not path.is_file():
        return []

    data = json.loads(path.read_text())
    machines: list[Machine] = []

    for entry in data.get("hosts", []):
        name = entry.get("name", "")
        if not name:
            continue

        services = [
            Service(
                type=s.get("type", ""),
                name=s.get("name", ""),
                port=int(s.get("port", 0)),
                scheme=s.get("scheme", "http"),
                base_url=s.get("base_url", ""),
                api_key_env=s.get("api_key_env", ""),
                quality_profile=s.get("quality_profile", ""),
                root_folder=s.get("root_folder", ""),
            )
            for s in entry.get("services", [])
        ]

        machines.append(
            Machine(
                id=entry.get("id", name),
                name=name,
                hostname=entry.get("hostname", name),
                user=entry.get("user", ""),
                port=int(entry.get("port", 22)),
                os=entry.get("os", "other"),
                harness=entry.get("harness", "ssh" if entry.get("user") else "none"),
                groups=list(entry.get("groups", [])),
                aliases=list(entry.get("aliases", [])),
                tags=dict(entry.get("tags", {})),
                identity_file=entry.get("identity_file"),
                services=services,
            )
        )

    return machines


def find_machine(machines: list[Machine], target: str) -> list[Machine]:
    """Match a target string against machine id, name, or aliases (case-insensitive)."""
    t = target.lower()
    return [m for m in machines if t in (m.id.lower(), m.name.lower()) or t in (a.lower() for a in m.aliases)]


# --- Ansible INI import -----------------------------------------------------

# Group name hints -> OS family, used when converting an Ansible inventory
_OS_HINTS = [
    ("windows", "windows"),
    ("mac", "macos"),
    ("ios", "ios"),
    ("android", "android"),
    ("linux", "linux"),
    ("raspbian", "linux"),
    ("unraid", "linux"),
]


def _guess_os(group: str) -> str:
    g = group.lower()
    for hint, os_name in _OS_HINTS:
        if hint in g:
            return os_name
    return "other"


def parse_ansible_ini(path: Path) -> list[Machine]:
    """Parse an Ansible INI inventory into Machine objects (all hosts, not just aliased ones)."""
    machines: list[Machine] = []
    current_group: str | None = None
    group_vars: dict[str, dict[str, str]] = {}
    by_name: dict[str, Machine] = {}

    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        vars_match = re.match(r"^\[(.+):vars\]$", stripped)
        if vars_match:
            current_group = f"{vars_match.group(1)}:vars"
            group_vars.setdefault(vars_match.group(1), {})
            continue

        group_match = re.match(r"^\[([^:\]]+)\]$", stripped)
        if group_match:
            current_group = group_match.group(1)
            continue

        if current_group is None:
            continue

        if current_group.endswith(":vars"):
            group = current_group[: -len(":vars")]
            if "=" in stripped:
                k, v = stripped.split("=", 1)
                group_vars[group][k.strip()] = v.strip()
            continue

        parts = stripped.split()
        inv_hostname = parts[0]

        if inv_hostname in by_name:
            by_name[inv_hostname].groups.append(current_group)
            continue

        machine = _machine_from_host_line(inv_hostname, parts[1:], current_group)
        by_name[inv_hostname] = machine
        machines.append(machine)

    # Apply group vars (user fallback) and derive harness
    for machine in machines:
        _apply_group_vars(machine, group_vars)

    return machines


def _machine_from_host_line(
    inv_hostname: str, kv_parts: list[str], current_group: str
) -> Machine:
    kvs: dict[str, str] = {}
    for part in kv_parts:
        if "=" in part:
            k, v = part.split("=", 1)
            kvs[k] = v

    alias = kvs.get("ssh_alias", "")
    return Machine(
        id=inv_hostname,
        name=inv_hostname,
        hostname=kvs.get("ansible_host", inv_hostname),
        user=kvs.get("ssh_user", kvs.get("ansible_user", "")),
        port=int(kvs.get("ansible_port", "22")),
        os=_guess_os(current_group),
        groups=[current_group],
        aliases=[alias] if alias else [],
    )


def _apply_group_vars(machine: Machine, group_vars: dict[str, dict[str, str]]) -> None:
    """Fill the machine's user from group vars and derive its harness."""
    if not machine.user:
        for group in machine.groups:
            gv = group_vars.get(group, {})
            if gv.get("ansible_user"):
                machine.user = gv["ansible_user"]
                break
    if machine.user:
        machine.harness = "ssh"
    elif machine.hostname != machine.name or re.match(r"^\d+\.\d+\.\d+\.\d+$", machine.hostname):
        machine.harness = "ping"
    else:
        machine.harness = "none"


def machines_to_json(machines: list[Machine]) -> str:
    """Serialize machines to the hosts.json format."""
    hosts = []
    for m in machines:
        entry: dict = {"name": m.name}
        if m.hostname != m.name:
            entry["hostname"] = m.hostname
        if m.user:
            entry["user"] = m.user
        if m.port != 22:
            entry["port"] = m.port
        entry["os"] = m.os
        entry["harness"] = m.harness
        entry["groups"] = m.groups
        if m.aliases:
            entry["aliases"] = m.aliases
        if m.tags:
            entry["tags"] = m.tags
        if m.identity_file:
            entry["identity_file"] = m.identity_file
        if m.services:
            entry["services"] = [
                {k: v for k, v in vars(s).items() if v not in ("", None)} for s in m.services
            ]
        hosts.append(entry)
    return json.dumps({"hosts": hosts}, indent=2)
