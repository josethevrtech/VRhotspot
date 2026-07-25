# Flatpak Desktop Companion

The optional Flatpak companion (`io.github.josethevrtech.VRhotspot`) is a
desktop tray app plus a locked Web Portal window for controlling the VR Hotspot
daemon. This document covers installation, automatic pairing, tray behavior,
authentication handling, and uninstall behavior in detail.

## Installing the companion

The guided installer asks `Install the Flatpak companion app?` and defaults to
No while the companion and its local packaging mature. Choosing No leaves the
existing daemon installation unchanged. Choosing Yes makes a best-effort,
user-scoped build/install from
`packaging/flatpak/io.github.josethevrtech.VRhotspot.json`.

Unattended installs do not install the companion by default. Explicitly opt in
with:

```bash
sudo bash /tmp/vrhotspot-install.sh --non-interactive \
  --install-flatpak-companion
```

The optional path requires `flatpak`, `flatpak-builder`, and the GNOME 50
runtime/SDK to already be available. It does not add Flathub or another Flatpak
remote. Missing prerequisites or a failed build are reported clearly, temporary
build files are removed, and the daemon install continues.

## Automatic pairing during install

After a successful companion install, the installer pairs the companion with
the freshly installed daemon automatically. It waits for the daemon health
endpoint, then runs `flatpak run io.github.josethevrtech.VRhotspot
--pair-token-stdin --save` as the original desktop user (never root) and feeds
the daemon token through the stdin pipe only—never through command-line
arguments, environment variables, or the Web UI. On success it launches
`flatpak run io.github.josethevrtech.VRhotspot --tray` detached, and the final
completion screen no longer asks for token copy/paste on that desktop. Remote
browsers still require manual authentication. If the desktop session bus is
unavailable, the daemon does not become healthy, pairing is rejected, or the
tray cannot be launched, the installer falls back to the existing manual Web
UI URL and token instructions unchanged.

## Tray and window behavior

The desktop launcher starts the companion in tray mode. Its only graphical UI
is the daemon-served Web Portal in a locked WebKitGTK window. Primary tray
activation opens or restores that same window, and the window close action
hides it to the tray without creating another window. Redundant Show and Hide
menu commands are not exported. If WebKit cannot be constructed, the companion
shows a bounded GTK error surface and does not open another interface. The tray
keeps **Current status** visible at the top. **Hotspot Commands** groups Start,
Stop, Restart, and Repair; **Network** contains Share Internet Connection; and
**Advanced** contains Authentication, Refresh Status, Open Diagnostics,
Privacy Mode, and the existing Start Hotspot Automatically setting. That
setting controls daemon hotspot autostart, not desktop-companion login
autostart. Launching VR Hotspot at desktop login remains deferred and is not
shown as a tray item. Explicit Quit exits only the desktop companion and does
not stop an already-running hotspot.

## Shared authentication and the Secret Service wallet

The Flatpak Web Portal window and tray share saved local authentication through
one native authentication controller and the app-specific Secret Service wallet.
Both modes load and validate that wallet entry against the bounded loopback API
at startup. Saving or replacing authentication in either mode is therefore
detected by the other; clearing it returns both to needs-authentication state on
their next prompt refresh. A rejected stale wallet entry is forgotten safely
instead of being retried indefinitely.

The locked page receives only token-free authentication readiness and
allowlisted daemon responses through the fixed-origin native broker. The wallet
token is never returned to JavaScript or placed in page HTML, browser storage,
URLs, labels, tooltips, notifications, logs, exception representations, or
smoke JSON. Selecting **Authenticate this device** in the Flatpak page opens the
native dialog; **Save for this desktop** controls Secret Service persistence.
If saving is not selected or the wallet is unavailable, the accepted token
remains local to that Flatpak process and is not shared through new IPC.

A normal local or remote browser is not connected to the Flatpak wallet or
native broker. Each browser page load requires explicit login, and the
browser-entered token is kept only in that page's memory—not Local Storage,
Session Storage, or IndexedDB.

**Authentication…** remains available for native token entry, replacement,
testing, and clearing. The companion never uses sudo to obtain a token and
never discovers one from `/etc/vr-hotspot/env`, `/var/lib/vr-hotspot`, daemon
configuration, environment variables, or command arguments. Missing or
rejected credentials are reported as **Needs Authentication**, separately from
**Daemon Unavailable** and unexpected **Error** states. Needs Authentication is
a static tray icon state; working/pulsing indication is reserved for active
transitions. Every tray state uses the VR Hotspot icon family instead of a
generic system Wi-Fi icon: Stopped has a red/off indicator, Running has a
green/on indicator, Transitioning has an amber/working indicator, and
authentication, daemon-unavailable, and error states retain the VR mark with a
red error-style indicator. The WebKit host window and KDE task manager always
use the stable base `io.github.josethevrtech.VRhotspot` application icon; that
window/taskbar icon does not follow hotspot or tray state. Start, Stop, Restart,
and Repair remain disabled until the shared token authenticates successfully
and then become available according to the real daemon state. The historical
flag remains a compatibility alias for the same default graphical behavior:

```bash
flatpak run io.github.josethevrtech.VRhotspot --web-portal-shell
```

Launching the tray companion at desktop login is not implemented and remains
distinct from starting the hotspot automatically with the computer.

## Uninstall behavior

The uninstaller (and the installer's existing-install cleanup) also removes the
optional Flatpak companion when present: it stops the running companion/tray,
uninstalls `io.github.josethevrtech.VRhotspot` from the invoking desktop user's
user-scoped Flatpak (and best-effort from the system scope), and deletes only
that user's companion app data
(`~/.var/app/io.github.josethevrtech.VRhotspot`) and companion autostart entry
(`~/.config/autostart/io.github.josethevrtech.VRhotspot.desktop`). Every
companion step is best-effort: a missing `flatpak` binary, a missing app, an
already-stopped tray, or an undetectable desktop user never fails the
uninstall. Shared Flatpak runtimes, Flatpak remotes, unrelated Flatpak apps,
and unrelated autostart files are never touched.

Removing the app plus its app data removes all companion-local state. A token
the companion explicitly saved through the desktop keyring (Secret Service)
cannot be cleared without a live desktop session, so that keyring item may
remain; it only held the daemon API token, which the uninstall deletes and a
reinstall regenerates. Remove it from your keyring manager if desired.

## Related documents

- `FLATPAK_ARCHITECTURE_PLAN.md` — companion architecture plan
- `first-run-wizard.md` — first-run experience design
