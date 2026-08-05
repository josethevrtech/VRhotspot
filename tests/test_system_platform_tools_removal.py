from __future__ import annotations

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
                stdout="/usr/sbin/adb is owned by android-tools 35.0.2-1\n",
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    os_release = tmp_path / "os-release"
    os_release.write_text("ID=cachyos\n", encoding="utf-8")
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
            stdout="/usr/sbin/adb is owned by unexpected-package 1.0-1\n",
            stderr="",
        )

    os_release = tmp_path / "os-release"
    os_release.write_text("ID=arch\n", encoding="utf-8")
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
    os_release.write_text("ID=steamos\n", encoding="utf-8")
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
