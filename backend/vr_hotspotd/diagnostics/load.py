import shutil
import subprocess
from typing import List, Optional

DEFAULT_CURL_URL = "https://speed.cloudflare.com/__down?bytes=25000000"


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
        self.requested_mbps = float(mbps)
        self.effective_mbps = float(mbps)
        self.duration_s = int(duration_s)
        self.url = url
        self.iperf3_host = iperf3_host
        self.iperf3_port = int(iperf3_port)
        self.notes: List[str] = []
        self.started = False
        self.proc: Optional[subprocess.Popen] = None
        self.command: Optional[List[str]] = None

    def _bytes_per_sec(self) -> int:
        return max(1, int(self.effective_mbps * 1_000_000 / 8.0))

    def _build_curl_cmd(self, curl_bin: str) -> List[str]:
        url = self.url.strip() if self.url else DEFAULT_CURL_URL
        limit_bps = str(self._bytes_per_sec())
        max_time = str(self.duration_s + 2)
        return [
            curl_bin,
            "-L",
            "--silent",
            "--show-error",
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
        return [
            iperf3_bin,
            "-c",
            self.iperf3_host,
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
