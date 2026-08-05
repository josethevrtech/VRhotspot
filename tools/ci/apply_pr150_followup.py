from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: Path, marker: str, addition: str) -> None:
    text = path.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    if marker not in text:
        raise RuntimeError(f"{path}: marker not found: {marker!r}")
    path.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


# Modal copy and sizing.
js = ROOT / "assets/devhub_connection_wizard.js"
replace_once(
    js,
    "const PATHS = {\nstatus: '/v1/status',\ndevices: '/v1/devbridge/adb/devices',\nwireless: '/v1/devbridge/adb/enable-wireless',\ntools: '/v1/devbridge/tools/status',\n};",
    "const PATHS = {\nstatus: '/v1/status',\ndevices: '/v1/devbridge/adb/devices',\nwireless: '/v1/devbridge/adb/enable-wireless',\ntools: '/v1/devbridge/tools/status',\ntoolsRemove: '/v1/devbridge/tools/remove',\n};",
)
replace_once(
    js,
    "<div class=\"devhub-wizard-body\">\n<p>Wireless headset setup only works over the dedicated VRhotspot network. Start the hotspot before continuing. Developer Hub will not enable wireless ADB through another Wi-Fi network.</p>\n<div id=\"devhubHotspotPreconditionStatus\" class=\"devhub-precondition-status\"",
    "<div class=\"devhub-wizard-body\">\n<div class=\"devhub-precondition-copy\">\n<p><strong>Wireless headset setup only works through the dedicated VRhotspot network.</strong></p>\n<p>Start the hotspot before continuing. Developer Hub will never enable wireless ADB through another Wi-Fi network.</p>\n</div>\n<div id=\"devhubHotspotPreconditionStatus\" class=\"devhub-precondition-status\"",
)
replace_once(
    js,
    "return {\nsource: String(adb.source || 'missing'),\nmanaged: adb.managed && typeof adb.managed === 'object' ? adb.managed : {},\n};",
    "return {\nsource: String(adb.source || 'missing'),\nmanaged: adb.managed && typeof adb.managed === 'object' ? adb.managed : {},\nsystem: adb.system && typeof adb.system === 'object' ? adb.system : {},\n};",
)
insert_marker = "async function updateTools() {\n"
remove_handler = r'''function bindToolsRemoveButton() {
const current = el('devhubRemoveTools');
if (!current || current.dataset.vrhotspotRemoveBound === '1') return current;
const replacement = current.cloneNode(true);
replacement.dataset.vrhotspotRemoveBound = '1';
replacement.addEventListener('click', async () => {
const source = String(replacement.dataset.toolsSource || 'managed');
const adbPath = String(replacement.dataset.adbPath || '');
if (source === 'system') {
const confirmed = window.confirm(
`Uninstall the operating-system package that provides ${adbPath || 'adb'}? `
+ 'Other Android development tools on this computer may also use it.',
);
if (!confirmed) return;
}
replacement.disabled = true;
feedback(
source === 'system'
? 'Uninstalling the system ADB package...'
: 'Removing VRhotspot-managed Android Platform-Tools...',
'loading',
);
try {
const response = await call(PATHS.toolsRemove, {
method: 'POST',
body: JSON.stringify({ source, path: adbPath }),
});
const result = publicResult(response);
if (!response.ok || result.success !== true) {
feedback(resultMessage(response, resultCode(response)), 'error');
return;
}
feedback(result.message || 'ADB removal completed.', 'success');
el('devhubRefresh')?.click();
await updateTools();
} catch (error) {
feedback(String(error.message || error), 'error');
} finally {
replacement.disabled = false;
}
});
current.replaceWith(replacement);
return replacement;
}
'''
append_once(js, insert_marker, remove_handler)
replace_once(
    js,
    "const install = el('devhubInstallTools');\nconst remove = el('devhubRemoveTools');",
    "const install = el('devhubInstallTools');\nconst remove = bindToolsRemoveButton();",
)
replace_once(
    js,
    "if (broken) {\ninstall.hidden = false;\ntext(install, 'Repair Managed ADB');\nremove.hidden = false;",
    "if (broken) {\ninstall.hidden = false;\ntext(install, 'Repair Managed ADB');\nremove.hidden = false;\nremove.dataset.toolsSource = 'managed';\nremove.dataset.adbPath = String(model.managed.path || '');\ntext(remove, 'Remove Managed ADB');",
)
replace_once(
    js,
    "} else if (managed) {\ninstall.hidden = false;\ntext(install, 'Reinstall Managed ADB');\nremove.hidden = false;",
    "} else if (managed) {\ninstall.hidden = false;\ntext(install, 'Reinstall Managed ADB');\nremove.hidden = false;\nremove.dataset.toolsSource = 'managed';\nremove.dataset.adbPath = String(model.managed.path || '');\ntext(remove, 'Remove Managed ADB');",
)
replace_once(
    js,
    "} else if (model.source === 'system') {\nsection.hidden = true;\ninstall.hidden = true;\nremove.hidden = true;\nif (license) license.hidden = true;\ntext(status, 'System ADB ready');",
    "} else if (model.source === 'system') {\nsection.hidden = false;\ninstall.hidden = true;\nremove.hidden = false;\nremove.dataset.toolsSource = 'system';\nremove.dataset.adbPath = String(model.system.path || '');\ntext(remove, 'Uninstall System ADB');\nif (license) license.hidden = true;\ntext(status, 'System ADB ready');",
)
replace_once(
    js,
    "} else {\ninstall.hidden = false;\ntext(install, 'Install Managed ADB');\nremove.hidden = true;",
    "} else {\ninstall.hidden = false;\ntext(install, 'Install Managed ADB');\nremove.hidden = true;\nremove.dataset.toolsSource = 'managed';\nremove.dataset.adbPath = '';",
)

css = ROOT / "assets/devhub_connection_wizard.css"
replace_once(
    css,
    ".devhub-precondition-dialog {\n  width: min(620px, 100%);\n}\n",
    ".devhub-precondition-dialog {\n  width: min(760px, calc(100vw - 48px));\n  max-height: calc(100vh - 48px);\n}\n",
)
css_addition = r'''
body[data-ui-mode="advanced"] .devhub-precondition-dialog {
  width: min(760px, calc(100vw - 48px));
  max-height: calc(100vh - 48px);
}

body[data-ui-mode="advanced"] .devhub-precondition-dialog .devhub-wizard-body {
  min-height: 0;
  padding: 30px 28px;
}

.devhub-precondition-copy {
  display: grid;
  gap: 12px;
  max-width: 62ch;
}

.devhub-precondition-dialog .devhub-wizard-body p {
  margin: 0;
  color: var(--text-main);
  font-size: 18px;
  line-height: 1.65;
}

.devhub-precondition-dialog .devhub-wizard-body p + p {
  color: var(--text-muted);
  font-size: 16px;
}

.devhub-precondition-dialog .devhub-wizard-footer {
  flex-wrap: wrap;
}

.devhub-precondition-dialog .devhub-wizard-footer .btn {
  min-height: 46px;
}

'''
append_once(css, "@media (max-width: 760px) {\n", css_addition)

# Safe system-package removal owned by the detected adb executable.
manager = ROOT / "backend/vr_hotspotd/devtools/platform_tools_manager.py"
replace_once(manager, "import shutil\nimport stat\n", "import shutil\nimport stat\nimport subprocess\n")
manager_insert = r'''

def _read_os_release_id(path: Path = Path("/etc/os-release")) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("ID="):
                return line.partition("=")[2].strip().strip('"').lower()
    except OSError:
        return ""
    return ""


def _run_package_command(
    argv: list[str],
    *,
    runner: Callable[..., Any],
    timeout: float = 120.0,
) -> Any:
    return runner(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )


def remove_system_platform_tools(
    *,
    adb_path: Any,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
    os_release_path: Path = Path("/etc/os-release"),
) -> Dict[str, Any]:
    operation = "remove"
    raw_path = str(adb_path or "").strip()
    candidate = Path(raw_path)
    if not raw_path or not candidate.is_absolute() or candidate.name != "adb":
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="System ADB removal requires the detected absolute adb path.",
        )
    if raw_path.startswith(str(Path(DEVTOOLS_ROOT)) + os.sep):
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="The detected executable is VRhotspot-managed, not a system ADB package.",
        )
    if _read_os_release_id(os_release_path) in {"steamos", "bazzite"}:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="System ADB removal is unavailable on this immutable operating system.",
        )

    package = ""
    manager = ""
    owner_argv: list[str] = []
    remove_argv: list[str] = []
    pacman = which("pacman")
    dpkg_query = which("dpkg-query")
    rpm = which("rpm")
    if pacman:
        manager = "pacman"
        owner_argv = [pacman, "-Qo", raw_path]
    elif dpkg_query:
        manager = "apt"
        owner_argv = [dpkg_query, "-S", raw_path]
    elif rpm:
        manager = "rpm"
        owner_argv = [rpm, "-qf", "--qf", "%{NAME}\\n", raw_path]
    else:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="No supported package manager can identify the system ADB package.",
        )

    try:
        owner = _run_package_command(owner_argv, runner=runner, timeout=20.0)
    except Exception as exc:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message=f"Could not identify the system ADB package: {type(exc).__name__}",
        )
    if owner.returncode != 0:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="The detected ADB executable is not owned by a supported system package.",
        )

    output = str(owner.stdout or "").strip()
    if manager == "pacman":
        marker = " is owned by "
        if marker in output:
            package = output.split(marker, 1)[1].split()[0]
    elif manager == "apt":
        package = output.split(":", 1)[0].strip().split(":", 1)[0]
    else:
        package = output.splitlines()[0].strip() if output else ""

    allowed = {
        "pacman": {"android-tools"},
        "apt": {"adb", "android-tools-adb"},
        "rpm": {"android-tools"},
    }
    if package not in allowed[manager]:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="Refusing to remove an unrecognized package that provides adb.",
            data={"package": package or None, "package_manager": manager},
        )

    if manager == "pacman":
        remove_argv = [pacman, "--noconfirm", "-Rns", package]
    elif manager == "apt":
        apt_get = which("apt-get")
        if not apt_get:
            return _result(
                operation,
                success=False,
                result_code=RESULT_REMOVE_FAILED,
                message="apt-get is unavailable for system ADB removal.",
            )
        remove_argv = [apt_get, "-y", "remove", package]
    else:
        dnf = which("dnf5") or which("dnf")
        if not dnf:
            return _result(
                operation,
                success=False,
                result_code=RESULT_REMOVE_FAILED,
                message="dnf is unavailable for system ADB removal.",
            )
        remove_argv = [dnf, "-y", "remove", package]

    try:
        removed = _run_package_command(remove_argv, runner=runner)
    except Exception as exc:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message=f"System ADB package removal failed: {type(exc).__name__}",
        )
    if removed.returncode != 0:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message="The system package manager could not remove ADB.",
            data={"package": package, "package_manager": manager},
        )
    return _result(
        operation,
        success=True,
        result_code=RESULT_OK,
        message=f"Removed system ADB package {package}.",
        data={
            "removed": True,
            "source": "system",
            "package": package,
            "package_manager": manager,
            "adb_path": raw_path,
        },
    )
'''
append_once(manager, "\ndef remove_managed_platform_tools(\n", manager_insert)

api = ROOT / "backend/vr_hotspotd/devtools/devhub_api.py"
replace_once(
    api,
    "    install_managed_platform_tools,\n    remove_managed_platform_tools,\n)",
    "    install_managed_platform_tools,\n    remove_managed_platform_tools,\n    remove_system_platform_tools,\n)",
)
replace_once(
    api,
    "        if operation == \"install\":\n            result = install_managed_platform_tools(\n                license_accepted=request.get(\"license_accepted\")\n            )\n        else:\n            result = remove_managed_platform_tools()",
    "        if operation == \"install\":\n            result = install_managed_platform_tools(\n                license_accepted=request.get(\"license_accepted\")\n            )\n        elif request.get(\"source\") == \"system\":\n            result = remove_system_platform_tools(adb_path=request.get(\"path\"))\n        else:\n            result = remove_managed_platform_tools()",
)

ui_tests = ROOT / "tests/test_devhub_connection_wizard_ui.py"
replace_once(
    ui_tests,
    "        \"Repair Managed ADB\",\n        \"System ADB ready\",",
    "        \"Repair Managed ADB\",\n        \"Uninstall System ADB\",\n        \"System ADB ready\",",
)
replace_once(
    ui_tests,
    "    assert \"model.source === 'system'\" in source\n    assert \"model.managed.verified === false\" in source",
    "    assert \"model.source === 'system'\" in source\n    assert \"remove.dataset.toolsSource = 'system'\" in source\n    assert \"window.confirm\" in source\n    assert \"model.managed.verified === false\" in source",
)
replace_once(
    ui_tests,
    "    assert \"@media (max-width: 620px)\" in source\n    assert \"status-dot\" not in source",
    "    assert \"@media (max-width: 620px)\" in source\n    assert 'body[data-ui-mode=\"advanced\"] .devhub-precondition-dialog' in source\n    assert \"min-height: 0;\" in source\n    assert \"font-size: 18px;\" in source\n    assert \"status-dot\" not in source",
)

system_tests = ROOT / "tests/test_system_platform_tools_removal.py"
system_tests.write_text(r'''from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vr_hotspotd.devtools.platform_tools_manager import (
    RESULT_OK,
    RESULT_REMOVE_FAILED,
    remove_system_platform_tools,
)


def _which(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


def test_pacman_system_adb_removal_uses_fixed_shell_false_argv(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        if "-Qo" in argv:
            return SimpleNamespace(
                returncode=0,
                stdout="/usr/sbin/adb is owned by android-tools 35.0.2-1\\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    os_release = tmp_path / "os-release"
    os_release.write_text("ID=cachyos\\n", encoding="utf-8")
    result = remove_system_platform_tools(
        adb_path="/usr/sbin/adb",
        which=_which({"pacman": "/usr/bin/pacman"}),
        runner=runner,
        os_release_path=os_release,
    )

    assert result["success"] is True
    assert result["result_code"] == RESULT_OK
    assert calls[0][0] == ["/usr/bin/pacman", "-Qo", "/usr/sbin/adb"]
    assert calls[1][0] == [
        "/usr/bin/pacman",
        "--noconfirm",
        "-Rns",
        "android-tools",
    ]
    assert all(call[1]["shell"] is False for call in calls)


def test_system_adb_removal_rejects_unknown_owner_package(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(
            returncode=0,
            stdout="/usr/sbin/adb is owned by unexpected-package 1.0-1\\n",
            stderr="",
        )

    os_release = tmp_path / "os-release"
    os_release.write_text("ID=arch\\n", encoding="utf-8")
    result = remove_system_platform_tools(
        adb_path="/usr/sbin/adb",
        which=_which({"pacman": "/usr/bin/pacman"}),
        runner=runner,
        os_release_path=os_release,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_REMOVE_FAILED
    assert len(calls) == 1


def test_system_adb_removal_is_blocked_on_immutable_os(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=steamos\\n", encoding="utf-8")
    result = remove_system_platform_tools(
        adb_path="/usr/bin/adb",
        which=_which({"pacman": "/usr/bin/pacman"}),
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
        os_release_path=os_release,
    )

    assert result["success"] is False
    assert result["result_code"] == RESULT_REMOVE_FAILED
    assert "immutable" in result["message"].lower()


def test_system_adb_removal_requires_detected_absolute_adb_path() -> None:
    result = remove_system_platform_tools(adb_path="adb")

    assert result["success"] is False
    assert result["result_code"] == RESULT_REMOVE_FAILED
''', encoding="utf-8")

api_contract_tests = ROOT / "tests/test_api_devhub_system_adb_removal.py"
api_contract_tests.write_text(r'''from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVHUB_API = ROOT / "backend/vr_hotspotd/devtools/devhub_api.py"


def test_tools_remove_dispatches_system_source_to_system_package_removal() -> None:
    source = DEVHUB_API.read_text(encoding="utf-8")

    assert 'request.get("source") == "system"' in source
    assert 'remove_system_platform_tools(adb_path=request.get("path"))' in source
    assert "remove_managed_platform_tools()" in source
''', encoding="utf-8")

# Preserve the product decision in docs.
docs = ROOT / "docs/dev-bridge.md"
text = docs.read_text(encoding="utf-8")
addition = """
### Removing ADB from Development Tools

Development Tools now follows the detected ADB source. VRhotspot-managed ADB can be
reinstalled or removed. A system ADB installation exposes an **Uninstall System
ADB** action with an explicit confirmation; the daemon verifies that the detected
`adb` executable is owned by a known Android-tools package before invoking a fixed,
non-shell package-manager command. Immutable SteamOS and Bazzite hosts refuse system
package removal. When no ADB is detected, the UI offers the reviewed managed ADB
installation.

"""
if "### Removing ADB from Development Tools" not in text:
    docs.write_text(text.rstrip() + "\n\n" + addition, encoding="utf-8")

# Remove this one-shot automation from the resulting branch.
(ROOT / ".github/workflows/pr150-followup.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
