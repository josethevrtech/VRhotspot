# Advanced Installation & Configuration

This guide covers manual installation for developers or custom setups,
installer flags, configuration, firewall ports, autostart, and optional
performance tuning. Most users should use the one-command installer described
in the main `README.md`.

## Manual installation (developers / custom setups)

**1. Clone the repository:**

```bash
git clone https://github.com/josethevrtech/VRhotspot.git
cd VRhotspot
```

**2. Copy to system location:**

```bash
sudo mkdir -p /var/lib/vr-hotspot/app
sudo rsync -a ./ /var/lib/vr-hotspot/app/
```

Note: Installed deployments serve WebUI assets from `/var/lib/vr-hotspot/app/assets`.
When running from the repo, the backend prefers `./assets` first.

**3. Run the install script:**

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh
```

**Optional flags:**

- `--bind 0.0.0.0` - Allow access from other devices
- `--enable-autostart` - Start hotspot automatically on boot
- `--api-token <token>` - Use a specific API token

**4. Verify installation:**

```bash
curl -fsS http://127.0.0.1:8732/healthz && echo OK
sudo systemctl status vr-hotspotd.service
```

## Unattended install

Use unattended mode only for automation, support, or managed installs.
`--non-interactive` intentionally skips prompts and uses defaults:

```bash
curl -sSL https://raw.githubusercontent.com/josethevrtech/VRhotspot/main/install.sh -o /tmp/vrhotspot-install.sh
sudo bash /tmp/vrhotspot-install.sh --non-interactive
```

Unattended installs do not install the Flatpak companion by default; see
`flatpak-companion.md` for the opt-in flag.

## API token

The install script generates a secure API token. To retrieve it:

```bash
sudo cat /etc/vr-hotspot/env
```

Look for:

```bash
VR_HOTSPOTD_API_TOKEN=<your-token>
```

## Firewall ports

VR Hotspot listens on **TCP 8732**. The installer automatically opens this port in:

- firewalld (if active)
- ufw (if installed)

**Manual firewall configuration:**

```bash
# firewalld
sudo firewall-cmd --permanent --add-port=8732/tcp
sudo firewall-cmd --reload

# ufw
sudo ufw allow 8732/tcp
```

## Autostart on boot

This means **Start Hotspot Automatically** with the computer. It is separate
from launching the desktop tray companion at login. The setting coordinates
the canonical `autostart` config value with the existing
`vr-hotspot-autostart.service`.

After installing the Flatpak companion, use the tray toggle. During daemon
installation or repair, use the existing installer options:

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh --enable-autostart
```

Disable autostart:

```bash
cd /var/lib/vr-hotspot/app/backend/scripts
sudo ./install.sh --disable-autostart
```

## Performance tuning (optional)

Enable in the web UI under Advanced Mode:

**System tuning:**

- `wifi_power_save_disable` - Disable power saving on Wi-Fi
- `cpu_governor_performance` - Set CPU to performance mode
- `usb_autosuspend_disable` - Prevent USB adapter suspension
- `sysctl_tuning` - Kernel network stack optimizations
- `interrupt_coalescing` - Optimize network interrupts
- `cpu_affinity` - Pin processes to specific CPU cores

**QoS presets:**

- **Ultra Low Latency** - Strict priority + UDP optimization
- **Stability (VR)** - DSCP CS5 + cake qdisc (recommended for VR)
- **High Throughput** - DSCP AF42 + cake qdisc
- **Balanced** - DSCP AF41 + fq_codel
