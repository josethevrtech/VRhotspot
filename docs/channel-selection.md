# Pre-session channel selection and width correctness

Channel selection is one step inside the existing hotspot start flow, not a second
optimizer or a background service. It does not modify saved settings or switch
channels while a VR session is running.

## Safety and selection policy

- Discover the exact interface's PHY using the shared host-inventory parser. Check
  every named interface on that radio, not just the requested interface.
- Never scan when an AP, connected station, unknown interface type, or unknown
  link state is present. Even a dormant AP is conservatively refused. An unnamed
  interface anywhere in the inventory also prevents scanning because the shared
  parser cannot safely attribute its activity.
- Read the exact PHY's current channel permissions. Exclude disabled, no-IR,
  radar/DFS, no-20-MHz and no-OFDM channels. Honor advertised 40/80/160 MHz
  restrictions and require every constituent of a bonded channel.
- Recheck the radio immediately before a bounded scan. This protects against a
  connection appearing during capability collection, but cannot eliminate races
  with a separate network manager. Hotspot lifecycle must serialize its own
  start operations and retain adapter ownership protections.
- Include legal channels where no BSS was observed; do not choose only among
  already-occupied channels. Count each neighboring BSS once, and estimate its
  overlap across the complete requested width, including HT40, VHT80/160/
  80+80, HE 6 GHz advertisements and adjacent 2.4 GHz channels.
- Rank overlap weighted modestly by received signal. Prefer the current channel
  only when its score equals the best score; otherwise break ties by channel
  number. A failed/unparseable scan is **unknown**, never a perfect score.
- If scanning is unavailable, retain a current channel only when the exact PHY
  confirms its complete channel block is permitted. Otherwise return no choice
  and let normal startup validate its configured fallback.

In manual mode, unresolved widths default to 80 MHz for 5/6 GHz and 20 MHz for
2.4 GHz. Automatic VR settings below resolve an explicit 80/160 MHz width. Explicit 20/40/80/160 MHz
profiles use the same geometry as hostapd generation; falling back to 2.4 GHz
does not carry an 80/160 MHz request into that band. Explicit 2.4 GHz 40 MHz is
supported, but remains subject to hostapd coexistence checks. Automatic 6 GHz
selection uses PSC primaries 5, 21, ..., 229, restricted further by complete
channel-block availability: for example, primary 229 cannot form an 80 MHz
block within the band.

Each inventory/link command has a two-second deadline, PHY capabilities have a
three-second deadline, and the scan has an eight-second deadline. Inventory is
limited to eight same-PHY interfaces. No interface is brought down, retuned, or
disconnected to obtain a result.

## What the score means

This is a **BSS-overlap heuristic**, not a measured airtime-utilization percentage,
noise measurement, latency prediction, or certification that a channel is clean.
Missing/unrecognized width information is treated as a 20 MHz estimate. A scan
cannot reliably identify non-Wi-Fi interference, hidden transmitters, every
6 GHz BSS, or changing traffic load. No cumulative survey counter is labeled as
an interval measurement. Validate the resulting channel with actual streaming
and the existing diagnostics.

The code deliberately keeps regulatory permission, geometric validity, and
driver capability distinct. Valid geometry is never permission to transmit.
No code here changes the country code, bypasses DFS/no-IR, or overrides a driver
restriction.

## Correcting the requested width

One shared pure geometry module now serves the scanner, the existing 5 GHz
capability-probe candidate builders, and all three hostapd configuration writers.
It replaces their separate center-channel/block calculations.
The linux-router command builder uses the same geometry to override its
unconditional HT40+ default and correctly express 160 MHz requests.

The existing main 5 GHz startup flow still qualifies **80 MHz first**, regardless
of an explicit 20/40/160 MHz setting. Its optional 40 MHz recovery is used only
after 80 MHz fails and is disabled by Basic Mode. This pass does not expand that
policy: the lower-level writers now encode widths correctly, but that is not a
claim that main-flow 5 GHz 160 MHz streaming is enabled. USB adapters no longer
receive a forced channel 36; saved manual channels and guarded auto-selection
are passed into the same existing legal-candidate qualification flow.

The changes correct these concrete configuration defects:

- Hostapd's VHT/HE width enumeration is 0 for 20/40, 1 for 80, and 2 for
  160 MHz; 3 means 80+80, not 160.
- On 6 GHz, hostapd derives width from the operating class and center indexes.
  Always emitting operating class 131 contradicted an 80 MHz request. Generated
  classes now match 20/40/80/160 MHz: 131/132/133/134 respectively, with matching
  center indexes.
- HT40 secondary orientation is generated independently of short guard interval.
  Disabling SGI no longer accidentally removes the secondary channel required
  by 40/80/160 MHz operation.
- The 5 GHz probe no longer invents an unaligned 40 MHz pair such as 40+44.
  Complete 165–177 blocks share the same geometry as the config writers.
- Unsupported hostapd keys amsdu_frames and ampdu_density are removed.
  Aggregation negotiation remains with the driver; these names never configured
  it successfully through upstream hostapd.
- 6 GHz no longer receives HT SGI flags as if they controlled HE guard intervals;
  its SAE configuration explicitly uses H2E-only.

These are config-generation/unit-test guarantees, not proof that a particular
USB adapter can sustain VR. Check the actual running channel width and complete
a hardware/driver/headset streaming qualification run before recommending it.

## Primary references

Linux Wireless documents the capabilities, scan, and connection-status
interfaces in [About iw](https://wireless.docs.kernel.org/en/latest/en/users/documentation/iw.html).
The kernel's [nl80211 interface definitions](https://github.com/torvalds/linux/blob/master/include/uapi/linux/nl80211.h)
define no-IR and bandwidth restrictions. Hostapd's
[configuration reference](https://android.googlesource.com/platform/external/wpa_supplicant_8/+/refs/heads/master/hostapd/hostapd.conf)
documents width enums and 6 GHz operating-class behavior; its
[configuration parser](https://android.googlesource.com/platform/external/wpa_supplicant_8/+/refs/heads/master/hostapd/config_file.c)
is the source of accepted option names.


## Automatic VR settings

`radio_auto` defaults to true in Basic and Pro. The adapter inventory publishes
`automatic_radio`, derived from the same captured driver/regulatory facts as its
capability display. It chooses the widest verified non-DFS AP channel block
(160 MHz before 80 MHz), preferring 6 GHz over 5 GHz at equal width. Automatic
VR settings never choose 2.4 GHz or 20/40 MHz. With no qualified block, startup
stops before changing the network and the interface explains the 80 MHz minimum.

Every constituent 20 MHz channel must be enabled and permit AP initiation at
that width. HE capabilities must belong to AP mode in the same band; a client's
6 GHz / 160 MHz capability does not establish permission to host a 6 GHz AP.
Channel selection scans before startup when the radio is idle. If a scan is
unavailable, the verified channel from the capability snapshot is used. This is
a capability-based default, not a measured guarantee that 160 MHz will outperform
80 MHz in every RF environment. DFS and 320 MHz are not selected by this policy.

The daemon resolves the settings again at startup, including WPA3-SAE for
6 GHz, Wi-Fi power saving off, USB autosuspend off for USB adapters, and driver
controlled transmit power. Automatic starts disable the optional 40 MHz
fallback and stop on failure rather than falling through to 2.4 GHz. An actual
width below 80 MHz, or one that cannot be verified, is not reported as ready.
Manual Pro settings remain available by turning off automatic selection.

The UI displays the resolved band and width immediately when the adapter or
mode changes. Active connection measurements remain distinct from the chosen
settings; changing settings does not silently restart a live connection. Pro's
save/restart indication still applies. Saves are serialized, and a response to
an older save cannot overwrite newer unsaved edits. Headset-hosted connections
continue to target their existing 6 GHz / 160 MHz network, independent of the
laptop's AP capabilities.
