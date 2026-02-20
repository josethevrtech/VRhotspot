from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parents[1]

INSTALLER_FILES = (
    REPO_ROOT / "install.sh",
    REPO_ROOT / "uninstall.sh",
    REPO_ROOT / "backend/scripts/install.sh",
    REPO_ROOT / "backend/scripts/uninstall.sh",
)

SYSTEMD_TEMPLATE_FILES = (
    REPO_ROOT / "backend/systemd/vr-hotspotd.service",
    REPO_ROOT / "backend/systemd/vr-hotspot-autostart.service",
)

README_FILES = (REPO_ROOT / "README.md",)

CHECK_FILES = INSTALLER_FILES + SYSTEMD_TEMPLATE_FILES + README_FILES

CANONICAL_UNITS = {"vr-hotspotd.service", "vr-hotspot-autostart.service"}
# Backward-compat unit aliases are allowed only in cleanup/removal logic.
LEGACY_UNITS = {"vr-hotspotd-autostart.service"}

UNIT_TOKEN_RE = re.compile(r"\bvr-hotspot[a-z0-9-]*\.service\b")
SERVICE_HEREDOC_RE = re.compile(r"cat\s*>\s*[^\n]*\.service[^\n]*<<")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_units_use_canonical_names_or_known_legacy_aliases() -> None:
    for path in CHECK_FILES:
        text = _read(path)
        found_units = set(UNIT_TOKEN_RE.findall(text))
        unexpected_units = sorted(found_units - CANONICAL_UNITS - LEGACY_UNITS)
        assert not unexpected_units, (
            f"{path}: unexpected unit names found: {', '.join(unexpected_units)}"
        )


def test_legacy_units_appear_only_in_cleanup_or_removal_logic() -> None:
    for path in CHECK_FILES:
        lines = _read(path).splitlines()
        for idx, line in enumerate(lines, start=1):
            if not any(legacy in line for legacy in LEGACY_UNITS):
                continue

            assert path in INSTALLER_FILES, (
                f"{path}:{idx}: legacy unit names are only allowed in installer cleanup logic"
            )

            lowered = line.lower()
            is_cleanup_context = any(
                key in lowered
                for key in (
                    "legacy",
                    "cleanup",
                    "remove",
                    "rm -f",
                    "rm -rf",
                    "disable",
                    "stop",
                )
            )
            assert is_cleanup_context, (
                f"{path}:{idx}: legacy unit reference must be cleanup/removal logic"
            )


def test_installers_do_not_embed_systemd_unit_heredocs() -> None:
    for path in INSTALLER_FILES:
        text = _read(path)
        assert "[Unit]" not in text, f"{path}: inline systemd unit body detected"
        assert "<<EOF" not in text, f"{path}: EOF heredoc detected"
        assert "<<-EOF" not in text, f"{path}: EOF heredoc detected"
        assert "<<'EOF'" not in text, f"{path}: EOF heredoc detected"
        assert not SERVICE_HEREDOC_RE.search(text), (
            f"{path}: installer must not create *.service via heredoc"
        )
