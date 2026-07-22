import os
from typing import Any


def add_passphrase_arguments(parser: Any) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--passphrase")
    group.add_argument(
        "--passphrase-fd",
        type=int,
        help="read the passphrase from an inherited file descriptor",
    )


def read_passphrase(args: Any) -> str:
    passphrase_fd = getattr(args, "passphrase_fd", None)
    if passphrase_fd is None:
        passphrase = getattr(args, "passphrase", None)
        if not isinstance(passphrase, str):
            raise RuntimeError("passphrase_missing")
        return passphrase

    try:
        fd = int(passphrase_fd)
        if fd < 0:
            raise ValueError("negative file descriptor")
        with os.fdopen(fd, "rb", closefd=True) as stream:
            payload = stream.read()
        return payload.decode("utf-8")
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise RuntimeError("passphrase_fd_read_failed") from exc


def write_protected_text(path: str, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "w", encoding="utf-8", closefd=True)
        fd = -1
        with stream:
            stream.write(content)
    finally:
        if fd >= 0:
            os.close(fd)
