import subprocess
from typing import Tuple


def _run(args: list[str]) -> Tuple[bool, str]:
    """
    Run firewall-cmd. Returns (ok, combined_output).
    Never raises.
    """
    try:
        p = subprocess.run(
            ["/usr/bin/firewall-cmd", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        out = (p.stdout or "").strip()
        return (p.returncode == 0), out
    except Exception as e:
        return False, f"firewall-cmd spawn failed: {e}"


def is_running() -> bool:
    ok, out = _run(["--state"])
    return ok and out.strip() == "running"


def add_interface(zone: str, ifname: str) -> Tuple[bool, str]:
    return _run(["--zone", zone, "--add-interface", ifname])


def remove_interface(zone: str, ifname: str) -> Tuple[bool, str]:
    return _run(["--zone", zone, "--remove-interface", ifname])


def enable_masquerade(zone: str) -> Tuple[bool, str]:
    return _run(["--zone", zone, "--add-masquerade"])


def enable_forward(zone: str) -> Tuple[bool, str]:
    """
    Not all distros expose zone forward in the same way.
    Best-effort: try --add-forward if supported; otherwise return non-fatal failure.
    """
    ok, out = _run(["--zone", zone, "--add-forward"])
    if ok:
        return True, out

    # If unsupported, we keep going; NAT+ip_forward may still work depending on policy setup.
    return False, out
