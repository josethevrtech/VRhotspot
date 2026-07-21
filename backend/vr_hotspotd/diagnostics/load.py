import ipaddress
import re
import shutil
import subprocess
from typing import List, Optional
from urllib.parse import urlsplit

from vr_hotspotd.diagnostics import limits

DEFAULT_CURL_URL = "https://speed.cloudflare.com/__down?bytes=25000000"
_DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_network_host(value: str) -> str:
    host = str(value or "").strip()
    if not host or len(host) > limits.LOAD_MAX_HOST_LENGTH:
        raise ValueError("invalid network host")
    if host.startswith("-") or any(ch.isspace() or ord(ch) < 32 for ch in host):
        raise ValueError("invalid network host")

    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    dns_host = host[:-1] if host.endswith(".") else host
    try:
        ascii_host = dns_host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid network host") from exc
    if (
        not ascii_host
        or len(ascii_host) > limits.LOAD_MAX_HOST_LENGTH
        or any(not _DNS_LABEL_RE.fullmatch(label) for label in ascii_host.split("."))
    ):
        raise ValueError("invalid network host")
    return host


def validate_curl_url(value: str) -> str:
    url = str(value or "").strip() or DEFAULT_CURL_URL
    if len(url) > limits.LOAD_MAX_URL_LENGTH:
        raise ValueError("curl URL is too long")
    if "\\" in url or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ValueError("invalid curl URL")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid curl URL") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("unsupported curl URL scheme")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("invalid curl URL")
    validate_network_host(parsed.hostname)
    if port is not None and not limits.LOAD_MIN_PORT <= port <= limits.LOAD_MAX_PORT:
        raise ValueError("invalid curl URL port")
    return url


class LoadGenerator:
    def __init__(
        self,
        *,
        method: str,
        mbps: float,
        duration_s: int,
        url: str,
        iperf3_host: str,
        iperf3_port: int,
    ):
        self.method = (method or "").strip().lower() or "curl"
        self.requested_mbps = limits.clamp_float(
            mbps,
            min_value=limits.LOAD_MIN_MBPS,
            max_value=limits.LOAD_MAX_MBPS,
        )
        self.effective_mbps = self.requested_mbps
        self.duration_s = limits.clamp_int(
            duration_s,
            min_value=limits.LOAD_MIN_DURATION_S,
            max_value=limits.LOAD_MAX_DURATION_S,
        )
        self.url = str(url or "").strip()
        self.iperf3_host = str(iperf3_host or "").strip()
        self.iperf3_port = limits.clamp_int(
            iperf3_port,
            min_value=limits.LOAD_MIN_PORT,
            max_value=limits.LOAD_MAX_PORT,
        )
        self.notes: List[str] = []
        self.started = False
        self.proc: Optional[subprocess.Popen] = None
        self.command: Optional[List[str]] = None

    def _bytes_per_sec(self) -> int:
        return max(1, int(self.effective_mbps * 1_000_000 / 8.0))

    def _build_curl_cmd(self, curl_bin: str) -> List[str]:
        url = validate_curl_url(self.url)
        limit_bps = str(self._bytes_per_sec())
        max_time = str(self.duration_s + limits.LOAD_PROCESS_GRACE_S)
        return [
            curl_bin,
            "--disable",
            "--globoff",
            "--no-location",
            "--silent",
            "--show-error",
            "--proto",
            "=http,https",
            "--max-redirs",
            "0",
            "--output",
            "/dev/null",
            "--limit-rate",
            limit_bps,
            "--connect-timeout",
            "2",
            "--max-time",
            max_time,
            url,
        ]

    def _build_iperf3_cmd(self, iperf3_bin: str) -> List[str]:
        host = validate_network_host(self.iperf3_host)
        return [
            iperf3_bin,
            "-c",
            host,
            "-p",
            str(self.iperf3_port),
            "-t",
            str(self.duration_s),
            "-b",
            f"{self.effective_mbps}M",
            "-u",
        ]

    def start(self) -> None:
        if self.method not in ("curl", "iperf3"):
            self.notes.append("load_method_unavailable")
            self.method = "none"
            self.effective_mbps = 0.0
            return

        try:
            if self.method == "curl":
                curl_bin = shutil.which("curl")
                if not curl_bin:
                    self.notes.append("curl_not_found")
                    self.method = "none"
                    self.effective_mbps = 0.0
                    return
                cmd = self._build_curl_cmd(curl_bin)
            else:
                iperf3_bin = shutil.which("iperf3")
                if not iperf3_bin:
                    self.notes.append("iperf3_not_found")
                    self.method = "none"
                    self.effective_mbps = 0.0
                    return
                if not self.iperf3_host:
                    self.notes.append("iperf3_host_missing")
                    self.method = "none"
                    self.effective_mbps = 0.0
                    return
                cmd = self._build_iperf3_cmd(iperf3_bin)
        except ValueError:
            self.notes.append("load_params_invalid")
            self.method = "none"
            self.effective_mbps = 0.0
            return

        try:
            self.command = cmd
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.started = True
        except Exception as exc:
            self.notes.append(f"load_start_failed:{exc}")
            self.method = "none"
            self.effective_mbps = 0.0
            self.proc = None
            self.started = False

    def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def info(self) -> dict:
        effective = float(self.effective_mbps) if self.started else 0.0
        return {
            "method": self.method,
            "requested_mbps": float(self.requested_mbps),
            "effective_mbps": effective,
            "notes": list(self.notes),
            "started": bool(self.started),
        }
