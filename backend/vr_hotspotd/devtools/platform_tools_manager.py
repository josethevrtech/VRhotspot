"""Install, verify, and remove VRhotspot-managed Android Platform-Tools."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import tempfile
from typing import Any, Callable, Dict, Iterator, Mapping, Optional, Union
from urllib.request import Request, urlopen
import uuid
import zipfile

from vr_hotspotd.devtools.platform_tools import (
    DEVTOOLS_ROOT,
    PinError,
    load_platform_tools_pin,
    normalize_arch,
    pin_is_blocked,
)


MANIFEST_FILENAME = "manifest.json"
LOCK_FILENAME = ".platform-tools.lock"
DOWNLOAD_TIMEOUT_S = 45.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MANIFEST_SCHEMA_VERSION = 1

RESULT_OK = "ok"
RESULT_LICENSE_REQUIRED = "license_not_accepted"
RESULT_PIN_UNAVAILABLE = "pin_unavailable"
RESULT_IMPLEMENTATION_BLOCKED = "implementation_blocked"
RESULT_UNSUPPORTED_ARCH = "unsupported_arch"
RESULT_INSTALL_BUSY = "tools_install_busy"
RESULT_DOWNLOAD_FAILED = "download_failed"
RESULT_ARCHIVE_TOO_LARGE = "archive_too_large"
RESULT_CHECKSUM_MISMATCH = "checksum_mismatch"
RESULT_ARCHIVE_INVALID = "archive_invalid"
RESULT_INSTALL_FAILED = "install_failed"
RESULT_REMOVE_FAILED = "remove_failed"
RESULT_MANIFEST_INVALID = "manifest_invalid"

PathLike = Union[Path, str]


class PlatformToolsError(RuntimeError):
    """Expected managed-tools failure with a stable result code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _result(
    operation: str,
    *,
    success: bool,
    result_code: str,
    message: str,
    data: Optional[Mapping[str, Any]] = None,
    warnings: Optional[list[str]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "operation": operation,
        "success": success,
        "result_code": result_code,
        "message": message,
        "warnings": list(warnings or []),
        "data": dict(data or {}),
    }


def _managed_root(root: Optional[PathLike]) -> Path:
    candidate = Path(root if root is not None else DEVTOOLS_ROOT)
    if not candidate.is_absolute():
        raise PlatformToolsError(RESULT_INSTALL_FAILED, "managed tools root must be absolute")
    return candidate


def _manifest_path(root: Path) -> Path:
    return root / MANIFEST_FILENAME


def _platform_tools_path(root: Path) -> Path:
    return root / "platform-tools"


def _adb_path(root: Path) -> Path:
    return _platform_tools_path(root) / "adb"


def _ensure_root(root: Path) -> None:
    try:
        if root.exists() and root.is_symlink():
            raise PlatformToolsError(
                RESULT_INSTALL_FAILED,
                "managed tools root must not be a symlink",
            )
        root.mkdir(parents=True, exist_ok=True, mode=0o755)
        root.chmod(0o755)
    except PlatformToolsError:
        raise
    except OSError as exc:
        raise PlatformToolsError(
            RESULT_INSTALL_FAILED,
            f"cannot prepare managed tools root: {exc}",
        ) from exc


@contextmanager
def _install_lock(root: Path) -> Iterator[None]:
    _ensure_root(root)
    lock_path = root / LOCK_FILENAME
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise PlatformToolsError(
                    RESULT_INSTALL_BUSY,
                    "another managed-tools operation is running",
                ) from exc
            raise
        yield
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_runtime_pin(
    pin: Mapping[str, Any],
    host_machine: Optional[str],
) -> None:
    if pin_is_blocked(pin):
        raise PlatformToolsError(
            RESULT_IMPLEMENTATION_BLOCKED,
            "the reviewed Platform-Tools pin is not enabled for installation",
        )
    host_arch = normalize_arch(host_machine)
    pin_arch = normalize_arch(pin.get("arch"))
    if not host_arch or host_arch != pin_arch:
        raise PlatformToolsError(
            RESULT_UNSUPPORTED_ARCH,
            f"managed Platform-Tools require {pin_arch or 'the pinned architecture'}",
        )
    license_name = pin.get("license_name")
    terms_url = pin.get("license_terms_url")
    if not isinstance(license_name, str) or not license_name.strip():
        raise PlatformToolsError(
            RESULT_PIN_UNAVAILABLE,
            "the reviewed Platform-Tools license metadata is unavailable",
        )
    if not isinstance(terms_url, str) or not terms_url.startswith(
        "https://developer.android.com/"
    ):
        raise PlatformToolsError(
            RESULT_PIN_UNAVAILABLE,
            "the reviewed Platform-Tools license metadata is unavailable",
        )


def _safe_allowlist(pin: Mapping[str, Any]) -> list[str]:
    raw = pin.get("extract_allowlist")
    if not isinstance(raw, list) or not raw:
        raise PlatformToolsError(
            RESULT_ARCHIVE_INVALID,
            "the extraction allowlist is unavailable",
        )
    entries: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise PlatformToolsError(
                RESULT_ARCHIVE_INVALID,
                "the extraction allowlist is invalid",
            )
        normalized = PurePosixPath(value)
        if normalized.is_absolute() or ".." in normalized.parts or str(normalized) != value:
            raise PlatformToolsError(
                RESULT_ARCHIVE_INVALID,
                "the extraction allowlist contains an unsafe path",
            )
        if not value.startswith("platform-tools/"):
            raise PlatformToolsError(
                RESULT_ARCHIVE_INVALID,
                "the extraction allowlist escaped platform-tools",
            )
        entries.append(value)
    if (
        "platform-tools/adb" not in entries
        or "platform-tools/NOTICE.txt" not in entries
    ):
        raise PlatformToolsError(
            RESULT_ARCHIVE_INVALID,
            "the extraction allowlist is incomplete",
        )
    return entries


def _download_archive(
    pin: Mapping[str, Any],
    destination: Path,
    *,
    opener: Callable[..., Any] = urlopen,
) -> str:
    maximum = pin.get("max_archive_bytes")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        raise PlatformToolsError(
            RESULT_PIN_UNAVAILABLE,
            "the archive size limit is invalid",
        )
    request = Request(
        str(pin["url"]),
        headers={"User-Agent": "VRhotspot-Developer-Hub/1.1"},
        method="GET",
    )
    digest = hashlib.sha256()
    total = 0
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT_S) as response, destination.open(
            "xb"
        ) as output:
            content_length = (
                response.headers.get("Content-Length")
                if hasattr(response, "headers")
                else None
            )
            if content_length:
                try:
                    announced = int(content_length)
                except (TypeError, ValueError):
                    announced = 0
                if announced > maximum:
                    raise PlatformToolsError(
                        RESULT_ARCHIVE_TOO_LARGE,
                        "the archive exceeds the configured size limit",
                    )
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise PlatformToolsError(
                        RESULT_ARCHIVE_TOO_LARGE,
                        "the archive exceeds the configured size limit",
                    )
                digest.update(chunk)
                output.write(chunk)
    except PlatformToolsError:
        raise
    except Exception as exc:
        raise PlatformToolsError(
            RESULT_DOWNLOAD_FAILED,
            f"Platform-Tools download failed: {type(exc).__name__}",
        ) from exc
    if total <= 0:
        raise PlatformToolsError(
            RESULT_DOWNLOAD_FAILED,
            "Platform-Tools download returned an empty archive",
        )
    return digest.hexdigest()


def _zip_entry_is_regular(info: zipfile.ZipInfo) -> bool:
    unix_mode = info.external_attr >> 16
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        return False
    return file_type in {0, stat.S_IFREG}


def _extract_archive(
    archive: Path,
    staging_root: Path,
    pin: Mapping[str, Any],
) -> list[Dict[str, Any]]:
    allowlist = _safe_allowlist(pin)
    installed_files: list[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            by_name = {entry.filename: entry for entry in bundle.infolist()}
            missing = [entry for entry in allowlist if entry not in by_name]
            if missing:
                raise PlatformToolsError(
                    RESULT_ARCHIVE_INVALID,
                    "the archive is missing required Platform-Tools files",
                )
            for relative in allowlist:
                info = by_name[relative]
                if info.is_dir() or not _zip_entry_is_regular(info):
                    raise PlatformToolsError(
                        RESULT_ARCHIVE_INVALID,
                        f"unsupported archive entry: {relative}",
                    )
                if info.file_size < 0 or info.file_size > int(
                    pin["max_archive_bytes"]
                ):
                    raise PlatformToolsError(
                        RESULT_ARCHIVE_INVALID,
                        f"archive entry exceeds the size limit: {relative}",
                    )
                target = staging_root.joinpath(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                digest = hashlib.sha256()
                with bundle.open(info, "r") as source, target.open("xb") as output:
                    copied = 0
                    while True:
                        chunk = source.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > info.file_size or copied > int(
                            pin["max_archive_bytes"]
                        ):
                            raise PlatformToolsError(
                                RESULT_ARCHIVE_INVALID,
                                f"archive entry expanded beyond its declared size: {relative}",
                            )
                        digest.update(chunk)
                        output.write(chunk)
                mode = 0o755 if relative == "platform-tools/adb" else 0o644
                target.chmod(mode)
                installed_files.append(
                    {
                        "path": relative,
                        "sha256": digest.hexdigest(),
                        "mode": format(mode, "04o"),
                        "size": target.stat().st_size,
                    }
                )
    except PlatformToolsError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PlatformToolsError(
            RESULT_ARCHIVE_INVALID,
            f"cannot extract Platform-Tools archive: {type(exc).__name__}",
        ) from exc
    return installed_files


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                dict(manifest),
                output,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PlatformToolsError(
            RESULT_INSTALL_FAILED,
            f"cannot write managed-tools manifest: {exc}",
        ) from exc


def _activate_install(
    root: Path,
    staged_tree: Path,
    manifest: Mapping[str, Any],
) -> None:
    final_tree = _platform_tools_path(root)
    backup = root / f".platform-tools-backup-{uuid.uuid4().hex}"
    manifest_path = _manifest_path(root)
    had_tree = final_tree.exists() or final_tree.is_symlink()
    if had_tree and final_tree.is_symlink():
        raise PlatformToolsError(
            RESULT_INSTALL_FAILED,
            "existing managed Platform-Tools path is a symlink",
        )
    moved_old = False
    moved_new = False
    try:
        if had_tree:
            os.replace(final_tree, backup)
            moved_old = True
        os.replace(staged_tree, final_tree)
        moved_new = True
        _write_manifest(manifest_path, manifest)
    except Exception as exc:
        if moved_new:
            try:
                shutil.rmtree(final_tree)
            except OSError:
                pass
        if moved_old:
            try:
                os.replace(backup, final_tree)
            except OSError:
                pass
        if isinstance(exc, PlatformToolsError):
            raise
        raise PlatformToolsError(
            RESULT_INSTALL_FAILED,
            f"cannot activate managed Platform-Tools: {type(exc).__name__}",
        ) from exc
    finally:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlatformToolsError(
            RESULT_MANIFEST_INVALID,
            "managed-tools manifest is unreadable",
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
    ):
        raise PlatformToolsError(
            RESULT_MANIFEST_INVALID,
            "managed-tools manifest has an unsupported schema",
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PlatformToolsError(
            RESULT_MANIFEST_INVALID,
            "managed-tools manifest has no file ledger",
        )
    for entry in files:
        if not isinstance(entry, dict):
            raise PlatformToolsError(
                RESULT_MANIFEST_INVALID,
                "managed-tools manifest contains an invalid file entry",
            )
        relative = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative.startswith("platform-tools/")
            or ".." in PurePosixPath(relative).parts
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PlatformToolsError(
                RESULT_MANIFEST_INVALID,
                "managed-tools manifest contains an unsafe file entry",
            )
    return manifest


def inspect_managed_platform_tools(
    root: Optional[PathLike] = None,
) -> Dict[str, Any]:
    managed_root = _managed_root(root)
    tree = _platform_tools_path(managed_root)
    manifest_path = _manifest_path(managed_root)
    present = tree.exists() or tree.is_symlink() or manifest_path.exists()
    result: Dict[str, Any] = {
        "present": present,
        "installed": False,
        "verified": False if present else None,
        "version": None,
        "manifest_path": str(manifest_path),
        "error": None,
    }
    if not present:
        return result
    if (
        tree.is_symlink()
        or not tree.is_dir()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
    ):
        result["error"] = RESULT_MANIFEST_INVALID
        return result
    try:
        manifest = _load_manifest(manifest_path)
        for entry in manifest["files"]:
            relative = PurePosixPath(entry["path"])
            target = managed_root.joinpath(*relative.parts)
            if target.is_symlink() or not target.is_file():
                raise PlatformToolsError(
                    RESULT_MANIFEST_INVALID,
                    "managed-tools file is missing",
                )
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                raise PlatformToolsError(
                    RESULT_MANIFEST_INVALID,
                    "managed-tools file checksum mismatch",
                )
        adb = _adb_path(managed_root)
        if not os.access(adb, os.X_OK):
            raise PlatformToolsError(
                RESULT_MANIFEST_INVALID,
                "managed adb is not executable",
            )
    except (OSError, PlatformToolsError):
        result["error"] = RESULT_MANIFEST_INVALID
        return result
    result.update(
        {
            "installed": True,
            "verified": True,
            "version": manifest.get("version"),
            "error": None,
        }
    )
    return result


def install_managed_platform_tools(
    *,
    license_accepted: Any,
    root: Optional[PathLike] = None,
    pin_path: Optional[PathLike] = None,
    host_machine: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
    now: Optional[Callable[[], datetime]] = None,
) -> Dict[str, Any]:
    operation = "install"
    if license_accepted is not True:
        return _result(
            operation,
            success=False,
            result_code=RESULT_LICENSE_REQUIRED,
            message=(
                "Accept the Android SDK License Agreement to install Platform-Tools."
            ),
        )
    managed_root = _managed_root(root)
    try:
        try:
            pin = load_platform_tools_pin(
                Path(pin_path) if pin_path is not None else None
            )
        except PinError as exc:
            raise PlatformToolsError(
                RESULT_PIN_UNAVAILABLE,
                "the reviewed Platform-Tools pin is unavailable",
            ) from exc
        _validate_runtime_pin(
            pin,
            host_machine if host_machine is not None else platform.machine(),
        )
        with _install_lock(managed_root):
            staging = Path(
                tempfile.mkdtemp(
                    prefix=".platform-tools-staging-",
                    dir=managed_root,
                )
            )
            try:
                staging.chmod(0o700)
                archive = staging / "platform-tools.zip"
                actual_sha256 = _download_archive(
                    pin,
                    archive,
                    opener=opener,
                )
                if actual_sha256 != pin["archive_sha256"]:
                    raise PlatformToolsError(
                        RESULT_CHECKSUM_MISMATCH,
                        (
                            "downloaded Platform-Tools checksum did not match the "
                            "reviewed pin"
                        ),
                    )
                extraction = staging / "extract"
                extraction.mkdir(mode=0o700)
                files = _extract_archive(archive, extraction, pin)
                archive.unlink(missing_ok=True)
                timestamp = (
                    now or (lambda: datetime.now(timezone.utc))
                )()
                manifest = {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "version": pin["version"],
                    "source_url": pin["url"],
                    "archive_sha256": pin["archive_sha256"],
                    "installed_at": timestamp.astimezone(
                        timezone.utc
                    ).isoformat(),
                    "license_name": pin.get("license_name"),
                    "license_terms_url": pin.get("license_terms_url"),
                    "files": files,
                }
                _activate_install(
                    managed_root,
                    extraction / "platform-tools",
                    manifest,
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    except PlatformToolsError as exc:
        return _result(
            operation,
            success=False,
            result_code=exc.code,
            message=str(exc),
        )
    except Exception as exc:
        return _result(
            operation,
            success=False,
            result_code=RESULT_INSTALL_FAILED,
            message=(
                "managed Platform-Tools installation failed: "
                f"{type(exc).__name__}"
            ),
        )
    inspected = inspect_managed_platform_tools(managed_root)
    verified = bool(inspected.get("verified"))
    return _result(
        operation,
        success=verified,
        result_code=RESULT_OK if verified else RESULT_INSTALL_FAILED,
        message=(
            f"Installed Android Platform-Tools {pin['version']}."
            if verified
            else "Platform-Tools were installed but verification failed."
        ),
        data={
            "version": pin["version"],
            "adb_path": str(_adb_path(managed_root)),
            "verified": inspected.get("verified"),
        },
    )


def remove_managed_platform_tools(
    *,
    root: Optional[PathLike] = None,
) -> Dict[str, Any]:
    operation = "remove"
    managed_root = _managed_root(root)
    try:
        with _install_lock(managed_root):
            tree = _platform_tools_path(managed_root)
            manifest_path = _manifest_path(managed_root)
            if (
                not tree.exists()
                and not tree.is_symlink()
                and not manifest_path.exists()
            ):
                return _result(
                    operation,
                    success=True,
                    result_code=RESULT_OK,
                    message="Managed Platform-Tools are already absent.",
                    data={"removed": False},
                )
            if (
                tree.is_symlink()
                or not manifest_path.is_file()
                or manifest_path.is_symlink()
            ):
                raise PlatformToolsError(
                    RESULT_MANIFEST_INVALID,
                    (
                        "managed Platform-Tools cannot be removed without a valid "
                        "manifest"
                    ),
                )
            _load_manifest(manifest_path)
            if tree.exists():
                if not tree.is_dir():
                    raise PlatformToolsError(
                        RESULT_MANIFEST_INVALID,
                        "managed Platform-Tools path is not a directory",
                    )
                shutil.rmtree(tree)
            manifest_path.unlink(missing_ok=True)
    except PlatformToolsError as exc:
        return _result(
            operation,
            success=False,
            result_code=exc.code,
            message=str(exc),
        )
    except Exception as exc:
        return _result(
            operation,
            success=False,
            result_code=RESULT_REMOVE_FAILED,
            message=(
                "managed Platform-Tools removal failed: "
                f"{type(exc).__name__}"
            ),
        )
    return _result(
        operation,
        success=True,
        result_code=RESULT_OK,
        message="Removed VRhotspot-managed Android Platform-Tools.",
        data={
            "removed": True,
            "adb_path": str(_adb_path(managed_root)),
        },
    )
