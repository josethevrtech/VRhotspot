# First-Run Setup Wizard Design

Status: documentation-only design for the planned VR Hotspot v1.1.0 "It Just
Works" update. This document does not require runtime code, UI, backend, test,
or version metadata changes.

## Goals

- Help a new user move from API-token login to a working VR hotspot with safe
  defaults.
- Use the implemented Adapter Intelligence v2 endpoint,
  `GET /v1/adapters/readiness`, to choose the best adapter and explain why.
- Keep Basic Mode simple by hiding risky choices when a clearly better adapter
  exists.
- Give Advanced and Developer users enough context to understand tradeoffs
  without forcing them through the full Pro interface.
- Provide recovery paths for common first-run failures instead of leaving users
  on a generic start error.

## Non-Goals

- Do not replace the existing Basic or Pro control surfaces.
- Do not remove advanced controls for returning users.
- Do not expose unsupported 6 GHz or 40 MHz fallback paths as beginner choices.
- Do not persist API tokens, passphrases, or adapter guesses in new locations
  until the implementation has a backend config design.
- Do not mark v1.1.0 as released or update runtime version metadata as part of
  this design.

## Entry Points

The wizard should appear after successful API-token authentication when the
backend reports that setup has not been completed. Returning users should land
on the normal Basic UI unless they explicitly choose to rerun setup.

Suggested entry points:

- First successful login when `wizard_completed=false` or missing.
- A Basic Mode action such as "Run setup again".
- A recovery prompt after the selected adapter disappears or becomes
  unsupported.
- A future install-complete link that opens the web UI directly into setup.

If the API token is invalid or expired, the existing login splash remains the
correct first screen. The wizard should not duplicate authentication UI beyond
handling an auth failure while it is already running.

## Wizard Flow

### 1. Welcome

Purpose: confirm that VR Hotspot will create a dedicated Wi-Fi network for a VR
headset.

Content should stay short:

- The PC will become a hotspot.
- A USB Wi-Fi adapter is recommended.
- The wizard will pick the safest VR defaults and can be rerun later.

Primary action: `Get started`.

Secondary action: `Skip for now`, visible only when the existing config is
usable or the user is in Advanced/Developer flow. Skipping should not set
`wizard_completed=true` unless the user explicitly confirms that they do not
want first-run guidance.

### 2. Choose Experience Level

Options:

- `Beginner`: default. Hide risky/internal adapters when a better USB
  AP-capable adapter exists, use recommended 5 GHz/80 MHz defaults, avoid
  technical fallback options.
- `Advanced`: show all AP-capable adapters, readiness warnings, band choices,
  and configuration implications before start.
- `Developer`: show raw-ish reason codes, endpoint payload summaries, selected
  config diff, and links to diagnostics/log surfaces after completion.

The selected level should map to the persisted selected mode:

- `Beginner` -> Basic Mode.
- `Advanced` -> Pro Mode with guided defaults.
- `Developer` -> Pro Mode with additional diagnostics visible where supported.

### 3. Adapter Selection

The wizard should call the current `GET /v1/adapters/readiness` endpoint and use Adapter
Intelligence v2 fields:

- `recommended`
- `basic_mode_recommended`
- `adapters`
- `readiness_state`
- `six_ghz_state`
- `recommendation_score`
- `reason_codes`
- `basic_mode_visibility`
- `explanation`

Beginner behavior:

- Auto-select `basic_mode_recommended` when present.
- Show only adapters where `basic_mode_visibility.visible=true`.
- Hide risky/internal adapters such as `wlan0` when a better USB AP-capable,
  5 GHz, 80 MHz adapter exists.
- Show one concise explanation for the selected adapter, such as "Recommended
  because it is a USB adapter with AP mode, 5 GHz, and 80 MHz support."
- If no Basic-visible adapter exists, show the most actionable recovery state
  instead of a blank selector.

Advanced/Developer behavior:

- Show all physical adapters from readiness data.
- Mark unsupported or not-recommended adapters as disabled or warning choices.
- Preserve reason codes and 6 GHz state for troubleshooting.
- Allow explicit selection of a non-recommended adapter only after a warning
  confirmation.

### 4. Headset Target

Options:

- `Meta Quest`
- `Pico`
- `Steam Frame / SteamVR headset`
- `Generic VR headset`

The target should guide copy and defaults, not create hard device-specific
network behavior yet. Initial recommended defaults:

- Meta Quest: prefer stable 5 GHz/80 MHz unless 6 GHz readiness is supported
  and the implementation can confirm the target model supports it.
- Pico: prefer stable 5 GHz/80 MHz.
- Steam Frame / SteamVR headset: prefer stable 5 GHz/80 MHz; mention SteamVR
  streaming sensitivity to latency and packet loss.
- Generic VR headset: prefer stable 5 GHz/80 MHz.

6 GHz should be explained only when readiness data says it is supported or when
Advanced/Developer users inspect why it is unavailable.

### 5. Performance Priority

Options:

- `Most stable`: default for first run. Prefer conservative channel selection,
  5 GHz, 80 MHz when supported, and the stability-oriented QoS profile.
- `Lowest latency`: prefer low-latency QoS and avoid settings known to increase
  jitter. Still require 5 GHz/80 MHz in Basic Mode.
- `Highest bandwidth`: prefer high-throughput QoS and the best supported width,
  but do not allow 160 MHz to outrank stable 80 MHz unless readiness data and
  future channel checks support it.
- `Travel/headless MiniPC`: favor predictable recovery, autostart guidance,
  visible connection details, and repair actions suitable for no-monitor use.

The priority should become a persisted preference and should map to existing or
future QoS/config presets during implementation. It should not silently enable
high-risk system tuning.

### 6. Network Settings

Fields:

- SSID
- Passphrase

Validation:

- SSID is required and should fit Wi-Fi SSID limits.
- Passphrase must meet WPA2/WPA3 length requirements.
- Beginner flow should default to WPA2 unless 6 GHz is selected, where WPA3-SAE
  is required.
- The passphrase field should support reveal/hide and QR-code presentation
  after save/start, matching the existing UI behavior.

Sensitive values:

- Never echo the saved passphrase in summaries, logs, or support text.
- Show "Saved" or masked text after persistence.
- Require an explicit reveal action for any future passphrase reveal endpoint.

### 7. Band and Channel Recommendation

Beginner recommendation:

- Prefer 5 GHz with 80 MHz channel width.
- Explain that 5 GHz/80 MHz is preferred because it is widely supported by VR
  headsets, provides much better throughput and latency than 2.4 GHz, and is
  less fragile than early 6 GHz setups on systems where regulatory or hostapd
  support is uncertain.
- Do not expose 40 MHz fallback in Basic Mode. If 80 MHz is missing, show a
  recovery/warning state rather than offering 40 MHz as a normal beginner
  choice.
- Do not expose 2.4 GHz as a normal VR choice in Basic Mode.
- Mention 6 GHz only when readiness is supported, or when explaining to
  Advanced/Developer users why it is blocked.

Advanced/Developer recommendation:

- Show 5 GHz, 6 GHz, and 2.4 GHz only when the selected adapter and readiness
  data support or can explain them.
- For 6 GHz, require `six_ghz_state=supported` before recommending it.
- If `six_ghz_state=blocked_by_regdomain`, explain that the adapter may support
  6 GHz but the current regulatory domain prevents AP operation.
- Keep channel width choices visible in Pro surfaces, but clearly mark 80 MHz
  as the VR baseline.

### 8. Review Configuration

The review screen should show:

- Experience level / selected mode.
- Selected adapter and readiness state.
- Headset target.
- Performance priority.
- SSID.
- Security mode.
- Recommended band and width.
- Whether internet sharing is enabled.
- Any warnings that will affect start success.

The review screen should not show the raw passphrase. It may show whether a
passphrase is present.

Primary action: `Start hotspot`.

Secondary actions:

- `Back` to edit.
- `Save without starting`, available for Advanced/Developer users and possibly
  Beginner users when the adapter is temporarily unavailable.

### 9. Start Hotspot

The wizard should save the selected config, then call `POST /v1/start`.

Expected implementation sequence:

1. Validate wizard inputs client-side.
2. Refresh readiness if the adapter list is stale.
3. Save configuration through the future config API additions or existing
   config endpoint where compatible.
4. Start hotspot.
5. Poll status until running, failed, or timed out.
6. Persist `wizard_completed=true` only after a successful start or explicit
   user confirmation to finish without starting.

If start returns a recoverable result, the wizard should move to the recovery
screen with a targeted action instead of dropping the user into Pro logs.

### 10. Success and Recovery Screen

Success state should show:

- Hotspot running status.
- SSID.
- Selected adapter.
- Band/width/channel when known.
- QR code action.
- Copy SSID and copy passphrase actions, consistent with current Basic UI.
- A clear next action: connect the VR headset to the displayed network.

Recovery state should show:

- The user-friendly cause.
- The technical detail or reason code behind an expandable disclosure for
  Advanced/Developer users.
- One primary repair action when available.
- A secondary path to choose another adapter or change settings.

## Persisted State and Configuration

Future implementation should persist:

- `wizard_completed`: boolean.
- `selected_adapter`: interface name selected by the user or readiness model.
- `selected_mode`: `basic`, `advanced`, or `developer`.
- `ssid`: hotspot SSID.
- `passphrase`: stored only through the backend's secure config path.
- `preferred_band`: `5ghz`, `6ghz`, or `2.4ghz`.
- `performance_priority`: `most_stable`, `lowest_latency`,
  `highest_bandwidth`, or `travel_headless_minipc`.
- `headset_target`: `meta_quest`, `pico`, `steamvr`, or `generic_vr`.

Useful future metadata:

- `wizard_completed_at`.
- `wizard_version`.
- `last_readiness_adapter_signature`, based on stable adapter identifiers when
  available.
- `last_successful_start_at`.
- `last_successful_band`.

## Reset and Rerun Behavior

Reset setup wizard:

- Clear `wizard_completed`.
- Preserve existing SSID/passphrase and adapter config unless the user chooses
  a full network reset.
- Return the user to the Welcome step on next authenticated UI load.
- Keep API token/session handling unchanged.

Rerun after adapter change:

- If the persisted adapter is missing, renamed, no longer AP-capable, or falls
  below Basic Mode requirements, prompt the user to rerun adapter selection.
- If a better USB AP-capable 5 GHz/80 MHz adapter appears, Basic Mode may show
  a non-blocking recommendation to rerun setup.
- Do not automatically replace a working adapter for Advanced/Developer users
  without confirmation.
- Use readiness reason codes to explain why rerun is suggested.

## Basic Mode Rules

Basic Mode should be opinionated:

- Hide risky/internal adapters when a better USB AP-capable adapter exists.
- Prefer adapters with AP mode, 5 GHz, 80 MHz, valid regulatory state, and USB
  bus type.
- Explain 5 GHz/80 MHz as the best first-run VR baseline.
- Avoid exposing 40 MHz fallback as a Basic Mode choice.
- Avoid exposing 2.4 GHz as a normal VR choice.
- Explain 6 GHz only when readiness is supported, or when the user opens
  Advanced/Developer details.
- Continue preserving hidden adapters in Advanced Mode and diagnostics.

## Error and Recovery States

### No Adapter Found

Cause: readiness returns no physical Wi-Fi adapter.

User message: connect a USB Wi-Fi adapter that supports AP mode, 5 GHz, and
80 MHz, then refresh.

Actions:

- `Refresh adapters`
- Link to supported adapter guidance.
- Advanced/Developer: show whether `iw` was unavailable or inventory failed.

### Missing AP Mode

Cause: adapter exists but `supports_ap_mode=false`.

User message: this adapter can connect to Wi-Fi but cannot create a hotspot.

Actions:

- Choose another adapter.
- Plug in a recommended USB adapter.
- Developer: show reason code `missing_ap_mode`.

### Missing 5 GHz

Cause: selected adapter does not support usable 5 GHz frequencies.

User message: VR Hotspot needs 5 GHz for the beginner setup path.

Actions:

- Choose a different adapter.
- Advanced only: continue with limitations if the backend supports the selected
  fallback.

### Missing 80 MHz

Cause: selected adapter has 5 GHz but lacks 80 MHz readiness.

User message: this adapter may work poorly for VR because it cannot provide the
recommended 80 MHz channel width.

Actions:

- Choose a better adapter.
- Advanced only: continue with limitations where supported.
- Basic Mode should not offer 40 MHz fallback as a normal resolution.

### 6 GHz Blocked by Regdomain

Cause: `six_ghz_state=blocked_by_regdomain`.

User message: the adapter may support 6 GHz, but the current country/regulatory
state does not allow using it as an access point.

Actions:

- Use recommended 5 GHz.
- Review country/regulatory settings in Advanced/Developer mode.
- Do not recommend 6 GHz until readiness becomes supported.

### API Auth Failure

Cause: readiness, config save, or start returns unauthorized.

User message: the API token is no longer accepted.

Actions:

- Return to login.
- Preserve unsaved wizard selections in memory where possible, but do not save
  them without authentication.

### Start Failure

Cause: `POST /v1/start` fails or status polling reports a failed state.

User message: the hotspot did not start.

Actions:

- Show backend result code and friendly explanation.
- Offer `Repair network` when available.
- Offer `Choose another adapter`.
- Developer: show status/preflight details and recent sanitized log excerpt
  when future APIs support it.

### Firewall or Service Issue

Cause: service is unavailable, firewalld/forwarding setup fails, or the backend
reports firewall/service preflight errors.

User message: VR Hotspot could not prepare the network services needed for the
hotspot.

Actions:

- `Repair network`
- `Retry start`
- Advanced/Developer: show firewalld mode, service status, and relevant
  sanitized details.

## Proposed Future API and Config Additions

These are implementation candidates, not current requirements:

- `GET /v1/setup`: return wizard completion state, wizard version, selected
  mode, headset target, performance priority, and whether rerun is recommended.
- `POST /v1/setup`: persist wizard state and non-sensitive setup preferences.
- `POST /v1/setup/reset`: clear wizard completion state without deleting saved
  network config by default.
- `POST /v1/setup/recommendation`: accept headset target, priority, and selected
  adapter, then return recommended band/security/channel-width settings.
- Extend `GET /v1/adapters/readiness` with an optional adapter signature and
  stale-selection hints.
- Extend config with `headset_target`, `performance_priority`, and
  `wizard_completed`.
- Add structured start/preflight errors suitable for wizard recovery cards.
- Add sanitized support details that can be embedded in Developer recovery
  disclosures without exposing tokens or passphrases.

## Accessibility

- Every step should have one visible heading and predictable Back/Next controls.
- Use native buttons, inputs, selects, and radio groups where possible.
- Maintain keyboard-only operation through the full wizard.
- Move focus to the step heading after navigation and to the first invalid
  field after validation errors.
- Use `role="alert"` or equivalent live regions for validation and start
  failures.
- Do not rely on color alone for readiness; include text labels and icons or
  badges.
- Passphrase reveal controls need explicit accessible names and pressed state.
- QR code must have adjacent text fallback: SSID and copy actions.
- Keep technical disclosures collapsed by default for Beginner users, but
  reachable and labelled for screen readers when present.

## Mobile Browser Considerations

- The wizard should fit narrow phone screens because users may configure a
  headless MiniPC from another device.
- Use one-column step layouts on mobile.
- Keep primary actions sticky only if they do not cover form fields or browser
  UI.
- Avoid dense tables for adapter selection; use stacked rows with concise
  readiness labels.
- Use large enough tap targets for adapter, headset, and priority choices.
- Ensure long interface names, SSIDs, and reason labels wrap without
  overflowing.
- Avoid requiring hover tooltips; all help text must be reachable by tap and
  keyboard.
- Recovery screens should keep the primary action visible without hiding the
  error cause.

## Future Testing Plan

Backend/unit tests:

- Setup state defaults when no wizard config exists.
- Persisting `wizard_completed`, selected adapter, selected mode, SSID,
  passphrase, preferred band, priority, and headset target.
- Reset endpoint preserves network config by default.
- Rerun recommendation when selected adapter disappears or readiness degrades.
- Recommendation mapping from headset target and performance priority.
- Structured error mapping for no adapter, missing AP mode, missing 5 GHz,
  missing 80 MHz, 6 GHz regdomain block, auth failure, start failure, and
  firewall/service issue.

Frontend/unit tests:

- Wizard appears only after authenticated first run when setup is incomplete.
- Beginner adapter list hides Basic-hidden adapters.
- Advanced/Developer list preserves unsupported or not-recommended adapters
  with warnings.
- Basic Mode never exposes 40 MHz fallback or 2.4 GHz as normal first-run
  choices.
- 6 GHz copy appears only when readiness supports it or in technical
  explanations.
- Passphrase validation and masking behavior.
- Review screen omits raw passphrase.

Integration/e2e tests:

- Happy path: login, Beginner defaults, save config, start hotspot, success.
- No-adapter path.
- Adapter changes after completion prompt rerun.
- Auth expires during setup and returns to login.
- Start failure offers repair and retry.
- Mobile viewport step navigation and text wrapping.
- Keyboard-only navigation through all steps.

Manual hardware checks:

- Known good USB 5 GHz/80 MHz adapter.
- Internal `wlan0` plus external USB adapter.
- 6 GHz-capable adapter with valid readiness.
- 6 GHz-capable adapter blocked by global or no-IR regulatory state.
- Headless/MiniPC access from a phone browser.
