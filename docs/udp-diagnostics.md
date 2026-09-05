# UDP echo diagnostics

The existing `POST /v1/diagnostics/udp_latency` test measures **synthetic UDP
echo round-trip time**. It does not measure headset decoding, frame delivery,
motion-to-photon latency, one-way delay, or prove a setup is VR-qualified.
It runs only when requested; it does not start a traffic load or change Wi-Fi.

## Endpoint requirement

Use an explicitly authorized IPv4 target and port running a byte-for-byte UDP
echo endpoint. A compatible existing netcat/socat echo arrangement can reflect
the payload without understanding it. A Quest, WiVRn, SteamVR or iperf3 port is
**not** automatically a raw UDP echo endpoint. No reply can mean that no echo
service exists or a firewall blocks it; it is not proof of Wi-Fi packet loss.
The diagnostic never falls back to ICMP and never opens an echo listener.

Do not expose an unauthenticated UDP reflector to the internet. Restrict any
operator-provided endpoint to the intended test source, stop it after testing,
and use existing authenticated/bounded tooling when those controls are needed.
No new echo server is shipped by VRhotspot.

## What changed

- Monotonic timing avoids clock-adjustment artifacts. Send pacing is independent
  of reply arrival, so one missing or slow echo does not stall later probes.
- Replies must come from the exact requested IP and port and echo the complete
  payload, including the sequence, original monotonic timestamp and a fresh
  128-bit session nonce. Old sessions, altered data and unrelated traffic do not
  become RTT samples. A nonce is a correlation safeguard, not encryption or an
  authenticated endpoint handshake.
- Delayed and out-of-order replies remain valid within the measurement window.
  Duplicate, foreign-peer and malformed replies have separate counters and do
  not inflate received counts.
- Sending is bounded by both count and duration. The final outstanding replies
  get at most one second after the last probe. The default 10-second send window
  therefore finishes within an approximately 11-second wall-time budget; the
  maximum configured window is 20 seconds plus one second of reply grace.
  Operating-system scheduling may delay return slightly.
- The helper can accept a cancellation event, checked at most every 100ms when
  scheduled. It closes its socket on cancellation or errors and retains partial
  results. This does not by itself add API request cancellation.
- No catch-up bursts follow a scheduling stall. The existing limits remain:
  at most 100 probes/second, 2,000 probes and 1,472 payload bytes/probe, or
  2,944,000 sent payload bytes per test. Payloads now have a 32-byte minimum to
  contain the nonce and timing header. Receive buffering is fixed and a test
  stops after excessive incoming datagrams rather than processing an unbounded
  flood. This is a lightweight probe, not a VR-bitrate load test.

## Interpreting the result

The existing `sent`, `received`, `packet_loss_pct`, `rtt_ms`, `jitter_ms` and
`samples_ms` keys remain. `samples_ms` is ordered by transmitted sequence.
`rtt_ms.max` exposes the largest observed round-trip delay. Percentiles require
at least 2 samples for p50, 20 for p95, 100 for p99 and 1,000 for p99.9; an
under-sampled percentile is JSON `null`, not zero. These are minimum reporting
thresholds, **not** statistical confidence guarantees or a VR certification.

Additional fields distinguish:

- `late_replies`: valid RTT exceeds twice the requested probe interval; the
  explicit threshold is included. These are received packets, not packet loss.
- `out_of_order_replies`: a first valid reply arrives after a higher sequence.
- `duplicate_replies`, `foreign_peer_replies`, `invalid_replies`: ignored replies.
- `missing_packets`, `missing_bursts`, `max_missing_burst_packets`: unreturned
  sent sequences at test end. `max_missing_burst_nominal_ms` is packet count
  multiplied by the requested interval; it is not an observed freeze duration.
- `max_scheduling_delay_ms`: host-side delay issuing a planned probe, which can
  help distinguish a loaded test machine from a consistently paced test.
- `elapsed_s`, `sent_payload_bytes`, `requested_packets`, `cancelled`,
  `stop_reason`: the actual test budget, completion and partial-run context.

The legacy `jitter_ms.avg` name is preserved but means the mean absolute RTT
difference between consecutive transmitted sequences for which both replies
arrived. It is not synchronized one-way network jitter; lost sequence gaps are
not bridged. An interrupted/failed test is partial evidence, not a pass/fail
qualification. Compare probes with WiVRn frame timings and the longer passive
streaming capture described in [streaming stability](streaming-stability.md).
