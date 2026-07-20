"""Reusable, read-only host probes and pure output parsers.

This module is deliberately limited to observing host state.  It must not
contain commands that change interfaces, services, firewall rules, or files.
Callers keep their existing policy and result shapes while sharing this
bounded execution and parsing layer.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


Runner = Callable[..., Any]
Which = Callable[[str], Optional[str]]

_IW_WIPHY_RE = re.compile(r"^Wiphy\s+(phy\d+)")
_IW_FREQ_LINE_RE = re.compile(
    r"^\s*\*\s+(\d+(?:\.\d+)?)\s+MHz\s+\[(\d+)\](.*)$",
    re.IGNORECASE,
)
_IW_VHT_WIDTH_RE = re.compile(r"Supported Channel Width:\s*(.+)$", re.IGNORECASE)
_IW_HE_80_RE = re.compile(r"HE40/HE80(?:/5GHz)?", re.IGNORECASE)
_HE_IFTYPES_RE = re.compile(r"^\s*HE Iftypes:\s*(.+)$", re.IGNORECASE)


def _subprocess_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


@dataclass(frozen=True)
class CommandResult:
    """Normalized result from a bounded, read-only command."""

    argv: Tuple[str, ...]
    exit_status: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    missing: bool = False
    permission_denied: bool = False
    error: Optional[str] = None
    exception: Optional[BaseException] = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def returncode(self) -> int:
        """Compatibility spelling used by ``subprocess.CompletedProcess``."""
        return self.exit_status

    @property
    def ok(self) -> bool:
        return (
            self.exit_status == 0
            and not self.timed_out
            and not self.missing
            and not self.permission_denied
        )

    def combined_output(self, *, include_error: bool = True) -> str:
        text = self.stdout or ""
        if self.stderr:
            text += ("\n" if text else "") + self.stderr
        if include_error and self.error and not text:
            text = self.error
        return text.strip()


def run_command(
    argv: Sequence[str],
    *,
    timeout_s: Optional[float],
    merge_stderr: bool = False,
    env: Optional[Mapping[str, str]] = None,
    runner: Optional[Runner] = None,
) -> CommandResult:
    """Run one read-only command and normalize all completion states.

    ``runner`` is injectable so characterization tests never need to invoke
    real host commands.  The default is resolved at call time so pytest's
    subprocess safety guard remains effective.
    """

    normalized_argv = tuple(os.fspath(item) for item in argv)
    execute = runner or subprocess.run
    kwargs: Dict[str, Any] = {"text": True}
    if merge_stderr:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["capture_output"] = True
    if timeout_s is not None:
        kwargs["timeout"] = timeout_s
    if env is not None:
        kwargs["env"] = dict(env)

    try:
        completed = execute(list(normalized_argv), **kwargs)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            argv=normalized_argv,
            exit_status=124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            timed_out=True,
        )
    except OSError as exc:
        missing = isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT
        permission_denied = isinstance(exc, PermissionError) or exc.errno in (
            errno.EACCES,
            errno.EPERM,
        )
        return CommandResult(
            argv=normalized_argv,
            exit_status=127,
            missing=missing,
            permission_denied=permission_denied,
            error=f"{type(exc).__name__}: {exc}",
            exception=exc,
        )
    except Exception as exc:
        return CommandResult(
            argv=normalized_argv,
            exit_status=127,
            error=f"{type(exc).__name__}: {exc}",
            exception=exc,
        )

    return CommandResult(
        argv=normalized_argv,
        exit_status=int(completed.returncode),
        stdout=_subprocess_text(getattr(completed, "stdout", "")),
        stderr=_subprocess_text(getattr(completed, "stderr", "")),
    )


def split_tokens(value: object) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        tokens: List[str] = []
        for item in value:
            tokens.extend(split_tokens(item))
        return tokens
    return [
        item.strip().lower()
        for item in str(value).replace(",", " ").split()
        if item.strip()
    ]


def classify_os_flavor(info: Mapping[str, object]) -> Dict[str, Any]:
    """Preserve the runtime OS-family classification used by Wi-Fi probing."""

    tokens: List[str] = []
    for key in ("id", "id_like", "variant_id", "variant", "name"):
        tokens.extend(split_tokens(info.get(key)))

    flavor = "unknown"
    family: Optional[str] = None
    if "steamos" in tokens:
        flavor = "steamos"
        family = "arch"
    elif "bazzite" in tokens:
        flavor = "bazzite"
        family = "fedora"
    elif "fedora" in tokens and any(
        token in tokens
        for token in ("silverblue", "kinoite", "sericea", "onyx", "atomic", "ostree")
    ):
        flavor = "fedora_atomic"
        family = "fedora"
    elif "fedora" in tokens:
        flavor = "fedora"
        family = "fedora"
    elif any(token in tokens for token in ("ubuntu", "debian", "pop", "linuxmint")):
        flavor = "ubuntu_debian"
        family = "debian"
    elif any(token in tokens for token in ("arch", "cachyos")):
        flavor = "arch"
        family = "arch"

    return {
        "id": info.get("id"),
        "id_like": info.get("id_like"),
        "variant_id": info.get("variant_id"),
        "version_id": info.get("version_id"),
        "name": info.get("name"),
        "flavor": flavor,
        "family": family,
    }


def parse_iw_dev_interfaces(text: str) -> List[Dict[str, str]]:
    """Parse ``iw dev`` into interface/phy pairs."""

    interfaces: List[Dict[str, str]] = []
    current_phy: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("phy#"):
            number = line.split("#", 1)[1].strip()
            current_phy = f"phy{number}"
        elif line.startswith("Interface") and current_phy:
            parts = line.split()
            interfaces.append({"ifname": parts[1].strip(), "phy": current_phy})
    return interfaces


def split_wiphy_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        match = _IW_WIPHY_RE.match(line.strip())
        if match:
            current = match.group(1)
            sections.setdefault(current, []).append(line)
            continue
        if current is not None:
            sections[current].append(line)
    return {phy: "\n".join(lines) for phy, lines in sections.items()}


def parse_supported_interface_modes(text: str) -> Optional[List[str]]:
    """Return modes in the ``Supported interface modes`` section."""

    if not text or "Supported interface modes" not in text:
        return None
    modes: List[str] = []
    in_modes = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Supported interface modes"):
            in_modes = True
            continue
        if not in_modes:
            continue
        if line.startswith("*"):
            modes.append(line.lstrip("*").strip())
        elif line:
            break
    return modes


def supports_ap_mode(text: str, *, extended_variants: bool = False) -> Optional[bool]:
    modes = parse_supported_interface_modes(text)
    if modes is None:
        return None
    if extended_variants:
        for mode in modes:
            normalized = mode.upper()
            if (
                normalized == "AP"
                or normalized.startswith("AP/")
                or normalized.startswith("AP-")
            ):
                return True
        return False
    return any(mode in ("AP", "AP/VLAN") for mode in modes)


def parse_regulatory_domains(text: str) -> Dict[str, Any]:
    """Parse ``iw reg get`` without applying regulatory policy."""

    global_country: Optional[str] = None
    global_header: Optional[str] = None
    phys: Dict[str, Dict[str, Any]] = {}
    current_section = "global"
    current_phy: Optional[str] = None
    current_phy_source = "unknown"

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("phy#"):
            current_section = "phy"
            phy_number = line.split()[0].split("#", 1)[1]
            current_phy = f"phy{phy_number}"
            current_phy_source = (
                "self-managed" if "self-managed" in line else "kernel-managed"
            )
            phys.setdefault(
                current_phy,
                {
                    "country": None,
                    "source": current_phy_source,
                    "raw_header": None,
                },
            )
            phys[current_phy]["source"] = current_phy_source
            continue

        if line.startswith("country "):
            parts = line.split()
            country = parts[1].rstrip(":") if len(parts) >= 2 else None
            if current_section == "global":
                global_country = country
                global_header = line
            elif current_section == "phy" and current_phy:
                phys.setdefault(current_phy, {})
                phys[current_phy]["country"] = country
                phys[current_phy]["raw_header"] = line
                phys[current_phy].setdefault("source", current_phy_source)

    return {
        "global": {
            "country": global_country or "unknown",
            "raw_header": global_header,
        },
        "phys": phys,
    }


def he_iftypes_has_ap(text: str) -> Optional[bool]:
    seen = False
    for raw in text.splitlines():
        match = _HE_IFTYPES_RE.search(raw)
        if not match:
            continue
        seen = True
        for token in match.group(1).split(","):
            if token.strip().upper() in ("AP", "AP/VLAN", "AP-VLAN"):
                return True
    return False if seen else None


def supports_wifi6(text: str) -> bool:
    he_ap = he_iftypes_has_ap(text)
    if he_ap is True:
        return True
    if he_ap is False:
        return False
    lowered = text.lower()
    return "802.11ax" in lowered or "he capabilities" in lowered


def supports_80mhz(text: str) -> bool:
    """Preserve inventory's VHT80/HE80 capability heuristic."""

    if re.search(r"HE40/HE80/5GHz", text, re.IGNORECASE):
        return True

    vht_section = re.search(
        r"VHT Capabilities \(.*?\):(.*?)(?:\n\s*[A-Za-z]|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not vht_section:
        return False
    width_line = re.search(
        r"Supported Channel Width:(.*)",
        vht_section.group(1),
        re.IGNORECASE,
    )
    if width_line:
        value = width_line.group(1).strip().lower()
        if "160" in value or "neither 160 nor 80+80" in value:
            return True
        if "20/40" in value:
            return False
    return True


def parse_band_support(text: str) -> Dict[str, bool]:
    supports = {
        "supports_2ghz": False,
        "supports_5ghz": False,
        "supports_6ghz": False,
    }
    in_frequencies = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Frequencies:"):
            in_frequencies = True
            continue
        if in_frequencies and line and not line.startswith("*"):
            in_frequencies = False
        if not in_frequencies:
            continue

        match = _IW_FREQ_LINE_RE.match(line)
        if not match:
            continue
        try:
            mhz = int(float(match.group(1)))
        except Exception:
            continue
        lowered = line.lower()
        if "disabled" in lowered or "no ir" in lowered or "no-ir" in lowered:
            continue
        if 2400 <= mhz <= 2500:
            supports["supports_2ghz"] = True
        elif 4900 <= mhz <= 5900:
            supports["supports_5ghz"] = True
        elif 5925 <= mhz <= 7125:
            supports["supports_6ghz"] = True
    return supports


def parse_5ghz_channels(text: str) -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    in_frequencies = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Frequencies:"):
            in_frequencies = True
            continue
        if in_frequencies and line and not line.startswith("*"):
            in_frequencies = False
        if not in_frequencies:
            continue

        match = _IW_FREQ_LINE_RE.match(raw)
        if not match:
            continue
        try:
            mhz = int(float(match.group(1)))
            channel = int(match.group(2))
        except Exception:
            continue
        if not 4900 <= mhz <= 5900:
            continue
        flags = (match.group(3) or "").lower()
        channels.append(
            {
                "channel": channel,
                "freq_mhz": mhz,
                "disabled": "disabled" in flags,
                "no_ir": (
                    "no ir" in flags
                    or "no-ir" in flags
                    or "no_ir" in flags
                ),
                "dfs": "radar detection" in flags or "dfs" in flags,
                "flags": flags.strip(),
            }
        )
    return channels


def parse_vht_supports_80(text: str) -> Optional[bool]:
    if "VHT Capabilities" not in text:
        return None
    for line in text.splitlines():
        match = _IW_VHT_WIDTH_RE.search(line)
        if not match:
            continue
        value = match.group(1).strip().lower()
        if "20/40" in value and "80" not in value and "160" not in value:
            return False
        return True
    return True


def parse_he_supports_80(text: str) -> Optional[bool]:
    if _IW_HE_80_RE.search(text):
        return True
    return None


def parse_ap_managed_concurrency(text: str) -> Optional[bool]:
    """Preserve the existing, currently informational concurrency parser."""

    if not text or "valid interface combinations" not in text:
        return None

    found_managed = False
    found_ap = False
    found_total = False
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if "valid interface combinations" in line:
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("*"):
            found_managed = False
            found_ap = False
            found_total = False
        if "#{ managed }" in line:
            found_managed = True
        if "AP" in line:
            found_ap = True
        if "total <=" in line:
            found_total = True
        if found_managed and found_ap and found_total:
            return True
    return False


def parse_default_uplink(text: str) -> Optional[str]:
    """Return the first interface following ``dev`` in default-route output."""

    for raw in text.splitlines():
        parts = raw.strip().split()
        if "dev" not in parts:
            continue
        index = parts.index("dev")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def probe_default_uplink(
    *,
    which: Optional[Which] = None,
    runner: Optional[Runner] = None,
    raise_on_execution_error: bool = False,
) -> Optional[str]:
    """Read and parse the active default-route interface.

    The compatibility flag reflects the two legacy policies: network tuning
    treated execution errors as no uplink, while engine helpers propagated
    them.
    """

    resolve = which or shutil.which
    ip = resolve("ip") or "/usr/sbin/ip"
    result = run_command(
        [ip, "route", "show", "default"],
        timeout_s=None,
        runner=runner,
    )
    if result.exception is not None and raise_on_execution_error:
        raise result.exception
    if result.exception is not None:
        return None
    return parse_default_uplink(result.stdout)


def probe_network_manager(
    *,
    which: Optional[Which] = None,
    runner: Optional[Runner] = None,
) -> Dict[str, bool]:
    resolve = which or shutil.which
    nmcli = resolve("nmcli")
    running = False
    if nmcli:
        result = run_command(
            [nmcli, "-t", "-f", "RUNNING", "g"],
            timeout_s=1.0,
            runner=runner,
        )
        running = result.exit_status == 0 and result.combined_output() == "running"
    return {"nmcli": bool(nmcli), "running": running}


def probe_firewall_backends(
    *,
    which: Optional[Which] = None,
    runner: Optional[Runner] = None,
) -> Dict[str, Any]:
    """Preserve Wi-Fi probe firewall priority and result shape."""

    resolve = which or shutil.which
    firewall_cmd = resolve("firewall-cmd")
    firewalld_active = False
    if firewall_cmd:
        result = run_command(
            ["firewall-cmd", "--state"],
            timeout_s=1.0,
            runner=runner,
        )
        firewalld_active = (
            result.exit_status == 0 and result.combined_output() == "running"
        )

    ufw = resolve("ufw")
    ufw_active = False
    if ufw:
        result = run_command(["ufw", "status"], timeout_s=1.5, runner=runner)
        if result.exit_status == 0:
            for line in result.combined_output().splitlines():
                if "Status:" in line:
                    # Compatibility: the legacy probe used this substring
                    # check, so "inactive" also evaluates as active.
                    ufw_active = "active" in line.lower()
                    break

    nft_present = bool(resolve("nft"))
    iptables = resolve("iptables")
    iptables_variant: Optional[str] = None
    if iptables:
        result = run_command(
            [iptables, "--version"],
            timeout_s=1.0,
            runner=runner,
        )
        if result.exit_status != 0:
            iptables_variant = "iptables-unknown"
        else:
            lowered = result.combined_output().lower()
            if "nf_tables" in lowered or "nft" in lowered:
                iptables_variant = "iptables-nft"
            elif "legacy" in lowered:
                iptables_variant = "iptables-legacy"
            else:
                iptables_variant = "iptables-unknown"

    selected = "unknown"
    rationale = "no_firewall_detected"
    if firewalld_active:
        selected = "firewalld"
        rationale = "firewalld_running"
    elif ufw_active:
        selected = "ufw"
        rationale = "ufw_active"
    elif nft_present:
        selected = "nftables"
        rationale = "nft_present"
    elif iptables_variant:
        selected = "iptables"
        rationale = "iptables_present"

    return {
        "firewalld": {
            "available": bool(firewall_cmd),
            "active": firewalld_active,
        },
        "ufw": {"available": bool(ufw), "active": ufw_active},
        "nftables": {"available": nft_present},
        "iptables": {
            "available": iptables_variant is not None,
            "variant": iptables_variant,
        },
        "selected_backend": selected,
        "rationale": rationale,
    }
