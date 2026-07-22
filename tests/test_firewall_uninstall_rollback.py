import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOP_INSTALLER = ROOT / "install.sh"
BACKEND_INSTALLER = ROOT / "backend/scripts/install.sh"
TOP_UNINSTALLER = ROOT / "uninstall.sh"
BACKEND_UNINSTALLER = ROOT / "backend/scripts/uninstall.sh"
BASH = shutil.which("bash") or "/bin/bash"


FAKE_FIREWALLD = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state_path = Path(os.environ["FAKE_FIREWALLD_STATE"])
log_path = Path(os.environ["FAKE_FIREWALLD_LOG"])
with log_path.open("a", encoding="utf-8") as log_file:
    log_file.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_UNINSTALL_LOG"):
    with Path(os.environ["FAKE_UNINSTALL_LOG"]).open(
        "a", encoding="utf-8"
    ) as log_file:
        log_file.write(json.dumps({"command": "firewall-cmd", "args": args}) + "\n")

if args == ["--state"]:
    raise SystemExit(0 if os.environ.get("FAKE_FIREWALLD_RUNNING", "1") == "1" else 1)
if args == ["--get-default-zone"]:
    print(os.environ.get("FAKE_FIREWALLD_ZONE", "public"))
    raise SystemExit(0)
if len(args) == 1 and args[0].startswith("--get-zone-of-interface="):
    print(os.environ.get("FAKE_FIREWALLD_ZONE", "public"))
    raise SystemExit(0)

state = json.loads(state_path.read_text(encoding="utf-8"))

def operation(prefix):
    for index, argument in enumerate(args):
        if argument.startswith(prefix):
            return index, argument
    return None, None

query_index, query_arg = operation("--query-")
if query_arg is not None:
    add_args = list(args)
    add_args[query_index] = query_arg.replace("--query-", "--add-", 1)
    raise SystemExit(0 if add_args in state else 1)

add_index, add_arg = operation("--add-")
if add_arg is not None:
    if os.environ.get("FAKE_FIREWALLD_ADD_RC"):
        raise SystemExit(int(os.environ["FAKE_FIREWALLD_ADD_RC"]))
    if args not in state:
        state.append(args)
        state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

remove_index, remove_arg = operation("--remove-")
if remove_arg is not None:
    if os.environ.get("FAKE_FIREWALLD_REMOVE_RC"):
        print("forced firewalld remove failure", file=sys.stderr)
        raise SystemExit(int(os.environ["FAKE_FIREWALLD_REMOVE_RC"]))
    add_args = list(args)
    add_args[remove_index] = remove_arg.replace("--remove-", "--add-", 1)
    if add_args not in state:
        print("NOT_ENABLED", file=sys.stderr)
        raise SystemExit(1)
    state.remove(add_args)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

raise SystemExit(0)
"""


FAKE_UFW = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
state_path = Path(os.environ["FAKE_UFW_STATE"])
log_path = Path(os.environ["FAKE_UFW_LOG"])
with log_path.open("a", encoding="utf-8") as log_file:
    log_file.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_UNINSTALL_LOG"):
    with Path(os.environ["FAKE_UNINSTALL_LOG"]).open(
        "a", encoding="utf-8"
    ) as log_file:
        log_file.write(json.dumps({"command": "ufw", "args": args}) + "\n")

state = json.loads(state_path.read_text(encoding="utf-8"))
if args == ["show", "added"]:
    if os.environ.get("FAKE_UFW_SHOW_RC"):
        raise SystemExit(int(os.environ["FAKE_UFW_SHOW_RC"]))
    print("Added user rules:")
    for rule in state:
        print(f"ufw {rule}")
    raise SystemExit(0)

if args == ["allow", "8732/tcp"]:
    if "allow 8732/tcp" in state:
        print("Skipping adding existing rule")
    else:
        state.append("allow 8732/tcp")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        print("Rule added")
    raise SystemExit(0)

if args == ["--force", "delete", "allow", "8732/tcp"]:
    if os.environ.get("FAKE_UFW_REMOVE_RC"):
        print("forced UFW remove failure", file=sys.stderr)
        raise SystemExit(int(os.environ["FAKE_UFW_REMOVE_RC"]))
    if "allow 8732/tcp" not in state:
        print("Could not find a matching rule", file=sys.stderr)
        raise SystemExit(1)
    state.remove("allow 8732/tcp")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)

raise SystemExit(2)
"""


FAKE_IP = """#!/bin/sh
if [ "$1 $2 $3" = "route show default" ]; then
    echo "default via 192.0.2.1 dev enp1s0"
fi
"""


FAKE_UNINSTALL_COMMAND = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys

command = Path(sys.argv[0]).name
args = sys.argv[1:]
log_path = Path(os.environ["FAKE_UNINSTALL_LOG"])
with log_path.open("a", encoding="utf-8") as log_file:
    log_file.write(json.dumps({"command": command, "args": args}) + "\n")

if command == "rm":
    safe_root = Path(os.environ["FAKE_UNINSTALL_SAFE_ROOT"]).resolve()
    for argument in args:
        if argument.startswith("-"):
            continue
        if argument in {"/run/vr-hotspot", "/tmp/vr-hotspot-*"}:
            continue
        target = Path(argument).resolve()
        if target != safe_root and safe_root not in target.parents:
            print(f"refusing unsafe fake rm target: {target}", file=sys.stderr)
            raise SystemExit(97)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
"""


def make_executable(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_firewall_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    make_executable(fake_bin / "firewall-cmd", FAKE_FIREWALLD)
    make_executable(fake_bin / "ufw", FAKE_UFW)
    make_executable(fake_bin / "ip", FAKE_IP)

    firewalld_state = tmp_path / "firewalld-state.json"
    firewalld_log = tmp_path / "firewalld-calls.jsonl"
    ufw_state = tmp_path / "ufw-state.json"
    ufw_log = tmp_path / "ufw-calls.jsonl"
    firewalld_state.write_text("[]", encoding="utf-8")
    firewalld_log.write_text("", encoding="utf-8")
    ufw_state.write_text("[]", encoding="utf-8")
    ufw_log.write_text("", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_FIREWALLD_STATE": str(firewalld_state),
            "FAKE_FIREWALLD_LOG": str(firewalld_log),
            "FAKE_UFW_STATE": str(ufw_state),
            "FAKE_UFW_LOG": str(ufw_log),
        }
    )
    return env, firewalld_state, ufw_state


def fake_full_uninstall_environment(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    fake_bin = tmp_path / "bin"
    for command in ("systemctl", "rm", "clear"):
        make_executable(fake_bin / command, FAKE_UNINSTALL_COMMAND)
    (fake_bin / "python3").symlink_to(sys.executable)

    command_log = tmp_path / "uninstall-calls.jsonl"
    command_log.write_text("", encoding="utf-8")
    env.update(
        {
            "PATH": str(fake_bin),
            "FAKE_UNINSTALL_LOG": str(command_log),
            "FAKE_UNINSTALL_SAFE_ROOT": str(tmp_path),
        }
    )
    return env, firewalld_state, ufw_state, command_log


def fake_installer_cleanup_environment(
    tmp_path: Path,
) -> tuple[dict[str, str], Path, Path, Path]:
    env, firewalld_state, ufw_state, command_log = (
        fake_full_uninstall_environment(tmp_path)
    )
    make_executable(tmp_path / "bin/pkill", FAKE_UNINSTALL_COMMAND)
    return env, firewalld_state, ufw_state, command_log


def run_sourced(
    script_path: Path,
    commands: str,
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", f"source {shlex.quote(str(script_path))}\n{commands}"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_lines(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_ledger(path: Path, actions: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "actions": actions}), encoding="utf-8")
    path.chmod(0o600)


def run_full_uninstall(
    uninstaller: Path,
    *,
    env: dict[str, str],
    app_root: Path,
    config_dir: Path,
    systemd_dir: Path,
) -> subprocess.CompletedProcess[str]:
    ledger = app_root / "firewall-rules.json"
    commands = (
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\n'
        f'INSTALL_ROOT={shlex.quote(str(app_root))}\n'
        f'APP_ROOT={shlex.quote(str(app_root))}\n'
        f'CONFIG_DIR={shlex.quote(str(config_dir))}\n'
        f'SYSTEMD_DIR={shlex.quote(str(systemd_dir))}\n'
        "check_root() { return 0; }\n"
        "detect_os() { return 0; }\n"
        "die() { return 0; }\n"
        "main --force"
    )
    return run_sourced(uninstaller, commands, env=env)


def run_installer_cleanup(
    *,
    env: dict[str, str],
    install_root: Path,
    config_dir: Path,
    systemd_dir: Path,
) -> subprocess.CompletedProcess[str]:
    ledger = install_root / "firewall-rules.json"
    commands = (
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\n'
        f'INSTALL_ROOT={shlex.quote(str(install_root))}\n'
        f'CONFIG_DIR={shlex.quote(str(config_dir))}\n'
        f'SYSTEMD_DIR={shlex.quote(str(systemd_dir))}\n'
        "INTERACTIVE=0\n"
        "cleanup_previous_install"
    )
    return run_sourced(TOP_INSTALLER, commands, env=env)


def make_uninstall_cleanup_tree(
    tmp_path: Path,
    uninstaller: Path,
) -> tuple[Path, Path, Path]:
    app_root = tmp_path / "app-state"
    config_dir = tmp_path / "config"
    systemd_dir = tmp_path / "systemd"
    app_root.mkdir()
    config_dir.mkdir()
    systemd_dir.mkdir()
    (app_root / "installed-file").write_text("app", encoding="utf-8")
    (config_dir / "config-file").write_text("config", encoding="utf-8")
    for unit in (
        "vr-hotspotd.service",
        "vr-hotspot-autostart.service",
        "vr-hotspotd-autostart.service",
    ):
        (systemd_dir / unit).write_text("unit", encoding="utf-8")
    if uninstaller == BACKEND_UNINSTALLER:
        drop_in = systemd_dir / "vr-hotspotd.service.d"
        drop_in.mkdir()
        (drop_in / "override.conf").write_text("drop-in", encoding="utf-8")
    return app_root, config_dir, systemd_dir


def firewalld_action(
    action: str,
    scope: str,
    zone: str = "public",
    *,
    port: str | None = None,
) -> dict[str, str]:
    record = {
        "backend": "firewalld",
        "action": action,
        "scope": scope,
        "zone": zone,
    }
    if port is not None:
        record["port"] = port
    return record


def firewalld_add_args(
    action: str,
    scope: str,
    zone: str = "public",
    *,
    port: str | None = None,
) -> list[str]:
    args = ["--zone", zone]
    if scope == "permanent":
        args.insert(0, "--permanent")
    option = {
        "add-port": f"--add-port={port}",
        "add-masquerade": "--add-masquerade",
        "add-forward": "--add-forward",
    }[action]
    return [*args, option]


def test_remote_firewalld_api_rules_are_recorded_as_owned(tmp_path):
    env, firewalld_state, _ = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"

    result = run_sourced(
        TOP_INSTALLER,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nopen_remote_access_firewall',
        env=env,
    )

    assert result.returncode == 0, result.stderr
    actions = read_json(ledger)["actions"]
    assert actions == [
        firewalld_action("add-port", "runtime", port="8732/tcp"),
        firewalld_action("add-port", "permanent", port="8732/tcp"),
        firewalld_action(
            "add-port", "runtime", "FedoraWorkstation", port="8732/tcp"
        ),
        firewalld_action(
            "add-port", "permanent", "FedoraWorkstation", port="8732/tcp"
        ),
    ]
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert len(read_json(firewalld_state)) == 4
    firewalld_calls = (tmp_path / "firewalld-calls.jsonl").read_text(
        encoding="utf-8"
    )
    assert "--reload" not in firewalld_calls


def test_remote_ufw_api_rule_is_recorded_as_owned(tmp_path):
    env, _, ufw_state = fake_firewall_environment(tmp_path)
    env["FAKE_FIREWALLD_RUNNING"] = "0"
    ledger = tmp_path / "app-state/firewall-rules.json"

    result = run_sourced(
        TOP_INSTALLER,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nopen_remote_access_firewall',
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert read_json(ledger) == {
        "version": 1,
        "actions": [
            {"backend": "ufw", "action": "allow", "rule": "8732/tcp"}
        ],
    }
    assert read_json(ufw_state) == ["allow 8732/tcp"]
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "installer",
    (TOP_INSTALLER, BACKEND_INSTALLER),
    ids=("top-level", "backend"),
)
def test_installer_forwarding_and_masquerade_rules_are_recorded(
    tmp_path,
    installer,
):
    env, firewalld_state, _ = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"

    result = run_sourced(
        installer,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nenable_firewalld_uplink_forwarding',
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert read_json(ledger)["actions"] == [
        firewalld_action("add-masquerade", "runtime"),
        firewalld_action("add-forward", "runtime"),
        firewalld_action("add-masquerade", "permanent"),
        firewalld_action("add-forward", "permanent"),
    ]
    assert read_json(firewalld_state) == [
        firewalld_add_args("add-masquerade", "runtime"),
        firewalld_add_args("add-forward", "runtime"),
        firewalld_add_args("add-masquerade", "permanent"),
        firewalld_add_args("add-forward", "permanent"),
    ]


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_uninstall_removes_only_recorded_firewall_actions(tmp_path, uninstaller):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    owned_actions = [
        firewalld_action("add-port", "runtime", port="8732/tcp"),
        firewalld_action("add-port", "permanent", port="8732/tcp"),
        firewalld_action("add-masquerade", "permanent"),
        firewalld_action("add-forward", "permanent"),
        {"backend": "ufw", "action": "allow", "rule": "8732/tcp"},
    ]
    write_ledger(ledger, owned_actions)

    user_firewalld_rule = [
        "--permanent",
        "--zone",
        "trusted",
        "--add-port=9999/tcp",
    ]
    firewalld_state.write_text(
        json.dumps(
            [
                firewalld_add_args("add-port", "runtime", port="8732/tcp"),
                firewalld_add_args("add-port", "permanent", port="8732/tcp"),
                firewalld_add_args("add-masquerade", "permanent"),
                firewalld_add_args("add-forward", "permanent"),
                user_firewalld_rule,
            ]
        ),
        encoding="utf-8",
    )
    ufw_state.write_text(
        json.dumps(["allow 8732/tcp", "allow 22/tcp"]), encoding="utf-8"
    )

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert read_json(firewalld_state) == [user_firewalld_rule]
    assert read_json(ufw_state) == ["allow 22/tcp"]
    firewalld_calls = (tmp_path / "firewalld-calls.jsonl").read_text(
        encoding="utf-8"
    )
    assert "--reload" not in firewalld_calls


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_preexisting_firewall_rules_are_not_recorded_or_removed(
    tmp_path,
    uninstaller,
):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    existing_firewalld_rule = firewalld_add_args(
        "add-port", "permanent", port="8732/tcp"
    )
    firewalld_state.write_text(json.dumps([existing_firewalld_rule]), encoding="utf-8")
    ufw_state.write_text(json.dumps(["allow 8732/tcp"]), encoding="utf-8")

    install_result = run_sourced(
        TOP_INSTALLER,
        (
            f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\n'
            'ensure_firewalld_action "add-port" "permanent" "public" "8732/tcp"\n'
            "ensure_ufw_api_port"
        ),
        env=env,
    )
    uninstall_result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert install_result.returncode == 0, install_result.stderr
    assert uninstall_result.returncode == 0, uninstall_result.stderr
    assert not ledger.exists()
    assert read_json(firewalld_state) == [existing_firewalld_rule]
    assert read_json(ufw_state) == ["allow 8732/tcp"]


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_uninstall_is_idempotent_when_recorded_rules_are_already_absent(
    tmp_path,
    uninstaller,
):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    write_ledger(
        ledger,
        [
            firewalld_action("add-port", "permanent", port="8732/tcp"),
            {"backend": "ufw", "action": "allow", "rule": "8732/tcp"},
        ],
    )

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0
    assert "already be absent" in result.stdout
    assert read_json(firewalld_state) == []
    assert read_json(ufw_state) == []


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_uninstall_is_idempotent_when_ledger_is_missing(tmp_path, uninstaller):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "missing/firewall-rules.json"

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0
    assert "No VRHotspot firewall ledger found" in result.stdout
    assert read_json(firewalld_state) == []
    assert read_json(ufw_state) == []
    assert (tmp_path / "firewalld-calls.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "ufw-calls.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_uninstall_is_idempotent_when_firewall_tool_is_missing(
    tmp_path,
    uninstaller,
):
    ledger = tmp_path / "app-state/firewall-rules.json"
    write_ledger(
        ledger,
        [firewalld_action("add-port", "permanent", port="8732/tcp")],
    )
    python_only_bin = tmp_path / "python-only-bin"
    python_only_bin.mkdir()
    (python_only_bin / "python3").symlink_to(sys.executable)
    env = os.environ.copy()
    env["PATH"] = str(python_only_bin)

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0
    assert "firewall-cmd is unavailable" in result.stdout


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_uninstall_is_idempotent_when_firewall_commands_fail(
    tmp_path,
    uninstaller,
):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    firewalld_rule = firewalld_add_args(
        "add-port", "permanent", port="8732/tcp"
    )
    firewalld_state.write_text(json.dumps([firewalld_rule]), encoding="utf-8")
    ufw_state.write_text(json.dumps(["allow 8732/tcp"]), encoding="utf-8")
    env["FAKE_FIREWALLD_REMOVE_RC"] = "7"
    env["FAKE_UFW_REMOVE_RC"] = "8"
    write_ledger(
        ledger,
        [
            firewalld_action("add-port", "permanent", port="8732/tcp"),
            {"backend": "ufw", "action": "allow", "rule": "8732/tcp"},
        ],
    )

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0
    assert "exit 7" in result.stdout
    assert "exit 8" in result.stdout
    assert read_json(firewalld_state) == [firewalld_rule]
    assert read_json(ufw_state) == ["allow 8732/tcp"]


def ledger_with_invalid_record(record) -> str:
    return json.dumps(
        {
            "version": 1,
            "actions": [
                record,
                firewalld_action(
                    "add-port", "permanent", port="8732/tcp"
                ),
            ],
        }
    )


MALFORMED_LEDGER_CASES = (
    pytest.param("{not-json", id="invalid-json"),
    pytest.param(b"\xff\xfe", id="invalid-utf8"),
    pytest.param("[]", id="wrong-top-level-type"),
    pytest.param(
        json.dumps({"version": 1, "actions": {}}),
        id="wrong-actions-type",
    ),
    pytest.param(
        json.dumps({"version": 1, "actions": [], "type": "firewalld"}),
        id="unknown-top-level-field",
    ),
    pytest.param(
        ledger_with_invalid_record("not-an-object"),
        id="non-object-record",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "nftables",
                "action": "add-port",
                "scope": "permanent",
                "zone": "public",
                "port": "8732/tcp",
            }
        ),
        id="unknown-backend-type",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "type": "firewalld",
                "action": "add-port",
                "scope": "permanent",
                "zone": "public",
                "port": "8732/tcp",
            }
        ),
        id="missing-backend-type-field",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "remove-port",
                "scope": "permanent",
                "zone": "public",
                "port": "8732/tcp",
            }
        ),
        id="unknown-action",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "add-port",
                "scope": "global",
                "zone": "public",
                "port": "8732/tcp",
            }
        ),
        id="unknown-scope",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "add-port",
                "scope": "permanent",
            }
        ),
        id="missing-required-fields",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "add-port",
                "scope": "permanent",
                "zone": "public;flush",
                "port": "8732/tcp",
            }
        ),
        id="unsafe-zone",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "add-port",
                "scope": "permanent",
                "zone": "public",
                "port": "22/tcp",
            }
        ),
        id="unexpected-port",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "firewalld",
                "action": "add-port",
                "scope": "permanent",
                "zone": "public",
                "port": "8732/tcp",
                "type": "rich-rule",
            }
        ),
        id="unknown-record-field",
    ),
    pytest.param(
        ledger_with_invalid_record(
            {
                "backend": "ufw",
                "action": "allow",
                "rule": "8732/tcp",
                "scope": "permanent",
            }
        ),
        id="unexpected-ufw-scope",
    ),
)


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
@pytest.mark.parametrize(
    "ledger_contents",
    MALFORMED_LEDGER_CASES,
)
def test_malformed_firewall_ledgers_fail_closed(
    tmp_path,
    uninstaller,
    ledger_contents,
):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    ledger.parent.mkdir(parents=True)
    if isinstance(ledger_contents, bytes):
        ledger.write_bytes(ledger_contents)
    else:
        ledger.write_text(ledger_contents, encoding="utf-8")

    user_firewalld_rule = firewalld_add_args(
        "add-port", "permanent", "trusted", port="9999/tcp"
    )
    firewalld_state.write_text(json.dumps([user_firewalld_rule]), encoding="utf-8")
    ufw_state.write_text(json.dumps(["allow 22/tcp"]), encoding="utf-8")

    result = run_sourced(
        uninstaller,
        f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "ledger could not be read" in result.stdout.lower()
    assert "could not read firewall ledger" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()
    assert read_json(firewalld_state) == [user_firewalld_rule]
    assert read_json(ufw_state) == ["allow 22/tcp"]
    assert (tmp_path / "firewalld-calls.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "ufw-calls.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
def test_unreadable_firewall_ledger_warns_without_firewall_calls(
    tmp_path,
    uninstaller,
):
    env, firewalld_state, ufw_state = fake_firewall_environment(tmp_path)
    ledger = tmp_path / "app-state/firewall-rules.json"
    write_ledger(
        ledger,
        [firewalld_action("add-port", "permanent", port="8732/tcp")],
    )
    ledger.chmod(0)
    if os.access(ledger, os.R_OK):
        ledger.write_bytes(b"\xff\xfe")

    try:
        result = run_sourced(
            uninstaller,
            f'FIREWALL_LEDGER={shlex.quote(str(ledger))}\nrollback_owned_firewall_rules',
            env=env,
        )
    finally:
        ledger.chmod(0o600)

    assert result.returncode == 0, result.stderr
    assert "ledger could not be read" in result.stdout.lower()
    assert "could not read firewall ledger" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()
    assert read_json(firewalld_state) == []
    assert read_json(ufw_state) == []
    assert (tmp_path / "firewalld-calls.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "ufw-calls.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("ledger_contents", MALFORMED_LEDGER_CASES)
def test_installer_cleanup_malformed_ledgers_fail_closed_and_continue(
    tmp_path,
    ledger_contents,
):
    env, firewalld_state, ufw_state, command_log = (
        fake_installer_cleanup_environment(tmp_path)
    )
    install_root, config_dir, systemd_dir = make_uninstall_cleanup_tree(
        tmp_path,
        TOP_UNINSTALLER,
    )
    ledger = install_root / "firewall-rules.json"
    if isinstance(ledger_contents, bytes):
        ledger.write_bytes(ledger_contents)
    else:
        ledger.write_text(ledger_contents, encoding="utf-8")

    unrecorded_api_rule = firewalld_add_args(
        "add-port", "permanent", port="8732/tcp"
    )
    user_firewalld_rule = firewalld_add_args(
        "add-port", "permanent", "trusted", port="9999/tcp"
    )
    firewalld_state.write_text(
        json.dumps([unrecorded_api_rule, user_firewalld_rule]),
        encoding="utf-8",
    )
    ufw_state.write_text(
        json.dumps(["allow 8732/tcp", "allow 22/tcp"]),
        encoding="utf-8",
    )

    result = run_installer_cleanup(
        env=env,
        install_root=install_root,
        config_dir=config_dir,
        systemd_dir=systemd_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "ledger could not be read" in result.stdout.lower()
    assert "could not read firewall ledger" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()
    assert result.stdout.index("ledger could not be read") < result.stdout.index(
        "Removing files and directories"
    )
    assert "Cleanup complete" in result.stdout
    assert not install_root.exists()
    assert not config_dir.exists()
    for unit in (
        "vr-hotspotd.service",
        "vr-hotspot-autostart.service",
        "vr-hotspotd-autostart.service",
    ):
        assert not (systemd_dir / unit).exists()

    calls = read_json_lines(command_log)
    assert not [
        call for call in calls if call["command"] in {"firewall-cmd", "ufw"}
    ]
    assert not {"nft", "iptables", "sudo", "ip"}.intersection(
        call["command"] for call in calls
    )
    assert (tmp_path / "firewalld-calls.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "ufw-calls.jsonl").read_text(encoding="utf-8") == ""
    assert read_json(firewalld_state) == [
        unrecorded_api_rule,
        user_firewalld_rule,
    ]
    assert read_json(ufw_state) == ["allow 8732/tcp", "allow 22/tcp"]


def test_installer_cleanup_rolls_back_only_valid_recorded_rules(tmp_path):
    env, firewalld_state, ufw_state, command_log = (
        fake_installer_cleanup_environment(tmp_path)
    )
    install_root, config_dir, systemd_dir = make_uninstall_cleanup_tree(
        tmp_path,
        TOP_UNINSTALLER,
    )
    ledger = install_root / "firewall-rules.json"
    owned_firewalld_rule = firewalld_add_args(
        "add-port", "permanent", port="8732/tcp"
    )
    user_firewalld_rule = firewalld_add_args(
        "add-port", "permanent", "trusted", port="9999/tcp"
    )
    firewalld_state.write_text(
        json.dumps([owned_firewalld_rule, user_firewalld_rule]),
        encoding="utf-8",
    )
    ufw_state.write_text(
        json.dumps(["allow 8732/tcp", "allow 22/tcp"]),
        encoding="utf-8",
    )
    write_ledger(
        ledger,
        [
            firewalld_action("add-port", "permanent", port="8732/tcp"),
            {"backend": "ufw", "action": "allow", "rule": "8732/tcp"},
        ],
    )

    result = run_installer_cleanup(
        env=env,
        install_root=install_root,
        config_dir=config_dir,
        systemd_dir=systemd_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "Cleanup complete" in result.stdout
    assert not install_root.exists()
    assert not config_dir.exists()
    assert read_json(firewalld_state) == [user_firewalld_rule]
    assert read_json(ufw_state) == ["allow 22/tcp"]

    calls = read_json_lines(command_log)
    firewall_calls = [
        call for call in calls if call["command"] in {"firewall-cmd", "ufw"}
    ]
    assert firewall_calls == [
        {
            "command": "ufw",
            "args": ["--force", "delete", "allow", "8732/tcp"],
        },
        {
            "command": "firewall-cmd",
            "args": [
                "--permanent",
                "--zone",
                "public",
                "--remove-port=8732/tcp",
            ],
        },
    ]
    assert all(
        not {"--reload", "--complete-reload", "--reset"}.intersection(call["args"])
        and not any("*" in argument or "flush" in argument for argument in call["args"])
        for call in firewall_calls
    )
    assert not {"nft", "iptables", "sudo", "ip"}.intersection(
        call["command"] for call in calls
    )


@pytest.mark.parametrize(
    "uninstaller",
    (TOP_UNINSTALLER, BACKEND_UNINSTALLER),
    ids=("top-level", "backend"),
)
@pytest.mark.parametrize(
    "warning_case",
    ("malformed-ledger", "firewall-command-failure", "missing-firewall-tool"),
)
def test_full_uninstall_continues_cleanup_after_firewall_warning(
    tmp_path,
    uninstaller,
    warning_case,
):
    env, firewalld_state, ufw_state, command_log = (
        fake_full_uninstall_environment(tmp_path)
    )
    app_root, config_dir, systemd_dir = make_uninstall_cleanup_tree(
        tmp_path,
        uninstaller,
    )
    ledger = app_root / "firewall-rules.json"
    owned_rule = firewalld_add_args(
        "add-port", "permanent", port="8732/tcp"
    )
    user_rule = firewalld_add_args(
        "add-port", "permanent", "trusted", port="9999/tcp"
    )
    firewalld_state.write_text(
        json.dumps([owned_rule, user_rule]),
        encoding="utf-8",
    )
    ufw_state.write_text(json.dumps(["allow 22/tcp"]), encoding="utf-8")

    if warning_case == "malformed-ledger":
        ledger.write_text("{not-json", encoding="utf-8")
        warning_text = "ledger could not be read"
    else:
        write_ledger(
            ledger,
            [firewalld_action("add-port", "permanent", port="8732/tcp")],
        )
        if warning_case == "firewall-command-failure":
            env["FAKE_FIREWALLD_REMOVE_RC"] = "7"
            warning_text = "exit 7"
        else:
            (tmp_path / "bin/firewall-cmd").unlink()
            warning_text = "firewall-cmd is unavailable"

    result = run_full_uninstall(
        uninstaller,
        env=env,
        app_root=app_root,
        config_dir=config_dir,
        systemd_dir=systemd_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "traceback" not in result.stderr.lower()
    stdout = result.stdout.lower()
    cleanup_text = (
        "removing systemd service files"
        if uninstaller == TOP_UNINSTALLER
        else "removing systemd unit files"
    )
    app_cleanup_text = (
        "removing all application files and configuration"
        if uninstaller == TOP_UNINSTALLER
        else "removing application and configuration files"
    )
    assert stdout.index(warning_text) < stdout.index(cleanup_text)
    assert stdout.index(cleanup_text) < stdout.index(app_cleanup_text)
    assert not app_root.exists()
    assert not config_dir.exists()
    for unit in (
        "vr-hotspotd.service",
        "vr-hotspot-autostart.service",
        "vr-hotspotd-autostart.service",
    ):
        assert not (systemd_dir / unit).exists()
    if uninstaller == BACKEND_UNINSTALLER:
        assert not (systemd_dir / "vr-hotspotd.service.d").exists()

    calls = read_json_lines(command_log)
    systemctl_calls = [
        call["args"] for call in calls if call["command"] == "systemctl"
    ]
    for unit in (
        "vr-hotspotd.service",
        "vr-hotspot-autostart.service",
        "vr-hotspotd-autostart.service",
    ):
        expected = (
            ["stop", unit]
            if uninstaller == TOP_UNINSTALLER
            else ["disable", "--now", unit]
        )
        assert expected in systemctl_calls
        if uninstaller == TOP_UNINSTALLER:
            assert ["disable", unit] in systemctl_calls
    assert ["daemon-reload"] in systemctl_calls

    rm_targets = {
        argument
        for call in calls
        if call["command"] == "rm"
        for argument in call["args"]
        if not argument.startswith("-")
    }
    assert str(app_root) in rm_targets
    assert str(config_dir) in rm_targets
    for unit in (
        "vr-hotspotd.service",
        "vr-hotspot-autostart.service",
        "vr-hotspotd-autostart.service",
    ):
        assert str(systemd_dir / unit) in rm_targets

    firewall_calls = [
        call for call in calls if call["command"] in {"firewall-cmd", "ufw"}
    ]
    if warning_case == "firewall-command-failure":
        assert firewall_calls == [
            {
                "command": "firewall-cmd",
                "args": [
                    "--permanent",
                    "--zone",
                    "public",
                    "--remove-port=8732/tcp",
                ],
            }
        ]
        firewall_index = calls.index(firewall_calls[0])
        first_rm_index = next(
            index for index, call in enumerate(calls) if call["command"] == "rm"
        )
        assert firewall_index < first_rm_index
    else:
        assert firewall_calls == []
    assert all("--reload" not in call["args"] for call in firewall_calls)
    assert not {"nft", "iptables", "sudo", "ip"}.intersection(
        call["command"] for call in calls
    )
    assert read_json(firewalld_state) == [owned_rule, user_rule]
    assert read_json(ufw_state) == ["allow 22/tcp"]
