from collections.abc import Sequence
from functools import wraps
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


_ALLOW_REAL_SYSTEM_COMMANDS = "VR_HOTSPOT_TEST_ALLOW_REAL_SYSTEM_COMMANDS"
_BLOCKED_SYSTEM_COMMANDS = frozenset(
    {
        "nmcli",
        "iw",
        "iwctl",
        "rfkill",
        "systemctl",
        "firewall-cmd",
        "sudo",
        "ip",
        "iptables",
        "nft",
        "hostapd",
        "dnsmasq",
    }
)
_SHELLS = frozenset({"ash", "bash", "dash", "ksh", "sh", "zsh"})
_SHELL_CONTROL_TOKENS = frozenset({";", ";;", "&", "&&", "|", "||", "(", ")", "\n"})
_SHELL_LEADING_KEYWORDS = frozenset(
    {"!", "{", "do", "elif", "else", "if", "then", "time", "until", "while"}
)
_SHELL_TRAILING_KEYWORDS = frozenset({"}", "done", "esac", "fi"})


def _command_text(value):
    try:
        return os.fsdecode(value)
    except TypeError:
        return str(value)


def _executable_name(value):
    return os.path.basename(_command_text(value).rstrip("/"))


def _is_shell_assignment(value):
    name, separator, _ = value.partition("=")
    return bool(separator and name and name.replace("_", "a").isalnum() and not name[0].isdigit())


def _blocked_argv(argv):
    args = [_command_text(value) for value in argv]
    while args:
        executable = _executable_name(args[0])
        if executable in _BLOCKED_SYSTEM_COMMANDS:
            return executable

        if executable in _SHELLS:
            for index, argument in enumerate(args[1:], start=1):
                if argument == "-c" or (
                    argument.startswith("-") and "c" in argument[1:] and argument != "--"
                ):
                    if index + 1 < len(args):
                        return _blocked_shell_command(args[index + 1])
                    return None
            return None

        if executable == "env":
            args = args[1:]
            while args and (args[0].startswith("-") or _is_shell_assignment(args[0])):
                args = args[1:]
            continue

        if executable in {"exec", "nohup"}:
            args = args[1:]
            while args and args[0].startswith("-"):
                args = args[1:]
            continue

        if executable == "command":
            if any(argument in {"-v", "-V"} for argument in args[1:]):
                return None
            args = args[1:]
            while args and args[0].startswith("-"):
                args = args[1:]
            continue

        return None

    return None


def _blocked_shell_segment(segment):
    while segment and (
        segment[0] in _SHELL_LEADING_KEYWORDS or _is_shell_assignment(segment[0])
    ):
        segment = segment[1:]
    if not segment or segment[0] in _SHELL_TRAILING_KEYWORDS:
        return None
    return _blocked_argv(segment)


def _blocked_shell_command(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.commenters = ""
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None

    segment = []
    for token in tokens:
        if token in _SHELL_CONTROL_TOKENS:
            blocked = _blocked_shell_segment(segment)
            if blocked:
                return blocked
            segment = []
        else:
            segment.append(token)
    return _blocked_shell_segment(segment)


def _blocked_command(command):
    if isinstance(command, (str, bytes, os.PathLike)):
        return _blocked_shell_command(_command_text(command))
    if isinstance(command, Sequence):
        return _blocked_argv(command)
    return None


def _guard_execution_path(execution_path, original, blocked_attempts):
    @wraps(original)
    def guarded(*args, **kwargs):
        if os.environ.get(_ALLOW_REAL_SYSTEM_COMMANDS) != "1":
            command = args[0] if args else kwargs.get("args")
            blocked = _blocked_command(command)
            if blocked:
                message = (
                    f"pytest safety guard blocked real system command '{blocked}' "
                    f"via {execution_path}; mock the execution path or explicitly set "
                    f"{_ALLOW_REAL_SYSTEM_COMMANDS}=1"
                )
                blocked_attempts.append(message)
                raise AssertionError(message)
        return original(*args, **kwargs)

    return guarded


def _mock_missing_execution_path(original):
    @wraps(original)
    def missing(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        blocked = _blocked_command(command)
        if blocked:
            raise FileNotFoundError(f"mocked unavailable system command: {blocked}")
        return original(*args, **kwargs)

    return missing


@pytest.fixture(autouse=True)
def block_real_system_commands(monkeypatch):
    """Prevent unit tests from changing host network or system state."""
    blocked_attempts = []
    for name in ("run", "check_output", "check_call", "Popen"):
        original = getattr(subprocess, name)
        monkeypatch.setattr(
            subprocess,
            name,
            _guard_execution_path(f"subprocess.{name}", original, blocked_attempts),
        )
    for name in ("system", "popen"):
        original = getattr(os, name)
        monkeypatch.setattr(
            os,
            name,
            _guard_execution_path(f"os.{name}", original, blocked_attempts),
        )

    yield blocked_attempts

    if blocked_attempts:
        raise AssertionError(
            "pytest safety guard intercepted system command attempts that code under test "
            "may have caught:\n" + "\n".join(f"- {message}" for message in blocked_attempts)
        )


@pytest.fixture
def mock_missing_system_commands(monkeypatch):
    """Make protected system commands deterministically unavailable to an opted-in test."""
    for module, names in (
        (subprocess, ("run", "check_output", "check_call", "Popen")),
        (os, ("system", "popen")),
    ):
        for name in names:
            original = getattr(module, name)
            monkeypatch.setattr(module, name, _mock_missing_execution_path(original))
