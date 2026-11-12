# Video-load congestion test (2026-11-11)

**Question.** Forest runs show long periods with all four robots' C2 down at
once, while path loss sits at a moderate 9–24 dB. Is this congestion from the
four video uplinks?

**Answer: yes.** Video starts → every robot's C2 dies within a minute. Video
stops → C2 stays dead for **14–18+ minutes** while a backlog inside the OAI UE
drains.

## Setup

Forest stack, idle (no patrol), robots parked at their spawn points (all four
NLoS through canopy, nominal UL attenuation 24–30 dB), UAV at (0, 0, 40 m).
Pings at 1 Hz in both directions per robot (host → UE IP, and netns → 10.45.0.1).
0–126 s no load; 126–486 s the campaign's own four 1.5 Mb/s CBR video feeds
(`scripts/video_streams.py`, unchanged); then no load. No OAI, Open5GS or
Gazebo configuration changed. Files: `logs/congestion_test_20261111/`.

## Results

| Phase | Ping loss per robot | RTT | Seconds with all 8 probes lost |
|---|---|---|---|
| No load (2 min) | 0 % | 61–96 ms | 0 % |
| Video on (6 min) | 64–91 % | median 3–4 min | 64 % |
| Video off (first 2 min) | 100 % | – | 100 % |

* **Delay grows linearly from the first second of video.** Alpha's uplink probe
  RTT was 11 s at +0 s, 63 s at +14 s, 138 s at +34 s and 283 s at +74 s. Probes
  are not lost, only delayed, so it is a queue, not a radio failure.
* **The links carried only a fraction of the load.** Video goodput was 0.13–0.49 Mb/s
  per robot against 1.5 offered; one-way video latency was 2–3 min (median) and up
  to 5.6 min.
* **The queue is inside the OAI UE.** The kernel TUN qdisc shows a 0-byte backlog and
  0 drops: all ~79 MB per robot were accepted by the modem, which holds them
  in its own uplink buffers.
* **Recovery is slow.** Video stopped at 16:10:20. Charlie answered pings again at 16:24,
  bravo at 16:26 and alpha at 16:27. **Delta had still not recovered at 16:28** (18 min).
  The weakest link has the biggest backlog relative to its rate.

## What this means

1. **The forest cell-wide outages are mainly congestion with very long memory, not
   coverage.** When links weaken (NLoS → lower MCS), their uplink capacity
   drops below the 1.5 Mb/s CBR video. The modem never drops, so the queue grows
   without bound and every packet, C2 included, waits minutes behind it. This
   also explains why outages did not line up with *instantaneous* path loss:
   the queue remembers minutes of past link quality.
2. **The failed "post-reattach uplink 100 % loss" reattaches fit this.** Killing the UE flushes
   its buffer, but the video restarts at once and refills it.
3. **For the data and models:** forest "C2 down" labels largely measure queue
   backlog driven by load, which is past link quality integrated over minutes. Altitude and position
   still matter through capacity, but with a lag of minutes that a 20 s horizon
   and a 30 s dwell cannot see. This weakens forest results for every policy
   compared so far.
4. **Open field is affected less** because its links mostly stay above 1.5 Mb/s
   per robot (the 1.5 Mb/s rate was calibrated there, 2026-10-23).

## Options (none applied)

* **Bound the queue outside OAI:** a small `tbf`/`fq_codel` rate limit or
  queue-length cap on each robot's TUN in its netns, so excess video is dropped,
  not queued. No OAI change, but it needs the link rate or an adaptive limit.
* **Adaptive video rate:** the sender backs off on loss or latency, as a real encoder would.
* **Bound the OAI UE RLC/PDCP buffers:** the cleanest fix, but it changes OAI
  configuration or code.
* **Priority for C2:** C2 shares the same bearer as video, so OAI has no QoS
  separation here.

## Caveats

One test, one placement, robots static. It does not show whether the four
UEs also compete for one shared uplink scheduler, because all four were overloaded on their own. A
one-robot-video run would separate the two.

## Follow-up: kernel queue cap (2026-11-11, 19:03–19:14)

Same test, same placement (UAV at 0,0,40 m; robot attenuation 23–30 dB UL, identical
channel-bridge rows to the first run), same four 1.5 Mb/s feeds. One change: after stack-up,
`cap.sh` put an HTB shaper on each robot's TUN inside its netns:
100 kbit/s ceiling (below delta's measured 0.13 Mb/s drain rate); C2/ICMP in a priority class;
video (UDP 5392–5407) in a low class with a 10-packet FIFO. No OAI/Open5GS/Gazebo change.
The cap lives only in the test scripts; campaign code is untouched.

| Phase | Ping loss (all 8 probes) | Ping RTT median | Seconds with all 8 probes lost |
|---|---|---|---|
| No load | 0 % | 60–93 ms | 0 % |
| Video on | **0 %** (was 64–91 %) | **69–87 ms** (was minutes) | **0 %** (was 64 %) |
| Video off | **0 %** (was 100 %) | 81–99 ms | **0 %** (was 100 %) |

* Video goodput 0.10 Mb/s per robot (the cap), latency median 1.08 s, max 1.16 s
  (was 2–3 min median, 5.6 min max). 1.1 s is the 10-packet FIFO draining at 100 kbit/s.
* The kernel dropped ~60,000 video packets per robot (~94 % of offered video);
  no C2/ICMP packet was dropped.
* No recovery period: links were clean the moment video stopped (was 14–18+ min).

**Conclusion.** Keeping the bottleneck in the kernel, below the radio's drain rate, removes
the outages completely at this placement. This confirms the mechanism: the outages are the
OAI UE buffer, not the radio.

**Limits.** 100 kbit/s is fixed and below every link here; it throws away 94 % of the video
and is far below what open-field links carry (≥1.5 Mb/s). A fixed cap above a link's capacity
would let the modem queue build again, more slowly. For campaigns, the cap must follow
the link (adaptive rate, or a cap set from the measured link quality), or the UE buffer
must be bounded in OAI. One run, one placement, robots stationary.

Data: `logs/congestion_test_cap_20261111/`.

## Follow-up 2: delay-adaptive cap (2026-11-11, 20:35–20:45)

Same test and placement again. The HTB tree from `cap.sh` (start 200 kbit/s, C2/ICMP priority,
video 10-packet FIFO) is driven by `adaptive_cap.py`: each robot pings the core at 5 Hz from its
netns (ICMP skips the kernel video queue, so its RTT only rises when the OAI UE backlogs);
median RTT of the last second > 60-s min + 150 ms -> cap x0.7 and hold 2 s; < min + 50 ms
and the cap is in use (>= 70 % sent) -> +50 kbit every 2 s; cap 50–1600 kbit/s. Inputs are
RTT and bytes sent only, never simulator path loss. No OAI/Open5GS/Gazebo change.

| | No cap | Fixed 100 kbit | Adaptive |
|---|---|---|---|
| Ping loss, video on | 64–91 % | 0 % | **0 %** |
| Ping RTT median, video on | minutes | 69–87 ms | **76–109 ms** |
| Seconds all 8 probes lost (on / off) | 64 % / 100 % | 0 / 0 | **0 / 0** |
| Recovery after video | 14–18+ min | none | **none** |
| Video goodput a/b/c/d (Mb/s) | 0.32/0.44/0.49/0.13 | 0.10 each | **0.30/0.37/0.42/0.10** |
| Video latency median | 2–3 min | 1.08 s | **0.30–0.42 s** (delta 1.08 s) |
| Video latency max | 5.6 min | 1.16 s | **2.2 s** |

* The cap tracked each link: medians alpha 305, bravo 386, charlie 429, delta 105 kbit/s,
  with 32–40 cuts per robot in 6 min (a sawtooth, as AIMD should).
* Adaptive video gets 85–94 % of what the saturated, uncapped links delivered, at
  sub-second latency instead of minutes, with C2 unaffected.
* Delta stays near the 50–100 kbit range; its latency is the 10-packet FIFO at that rate.

**Caveats.** One run, one placement, robots stationary, forest only. The rise rule
compares against the 60-s minimum RTT (~15–20 ms) while typical RTT is ~70–90 ms; it still
rose often enough here, but needs checking in open field (step 2), where the cap must climb
to 1.5 Mb/s. Not yet in campaign code.

Data: `logs/congestion_test_adaptive_20261111/`.

## Follow-up 3: delay-adaptive cap, open field (2026-11-11, 23:28–23:39)

Identical to follow-up 2 except `SAR_WORLD=sar_open_field` (UAV 0,0,40 m; UL attenuation
2–9 dB). Question: does the cap climb out of the way where links are strong?

* Ping loss 0 % in every phase; RTT 30–52 ms medians; no second with all probes lost.
* Each robot's cap climbed from 200 to >= 1500 kbit/s in 51–54 s of video, reached the
  1600 ceiling and stayed there: **0 cuts** in 6 min on any robot.
* Video goodput **1.55 Mb/s per robot** (the full feed), latency median 82–89 ms,
  p90 118–131 ms, max 546–555 ms (the max falls in the ramp-up).

**Reading.** In open field the cap does not throttle once it has ramped; in forest (follow-up 2)
it tracks each weak link. The cost is the ~50 s linear ramp (+50 kbit/2 s) when video starts,
during which the feed is under-served; a faster start (multiplicative increase until the
first cut) would shorten it. No uncapped open-field run was made alongside, but delivering the
full offered rate means the cap was not the limit after ramp-up.
(Log note: the controller labels steps at the 1600 ceiling "up" although the cap is unchanged.)

Data: `logs/congestion_test_adaptive_open_20261111/`.

## Follow-up 4: adaptive cap with robots on patrol (2026-11-11 23:46 – 11-12 00:16)

A 30-min forest campaign with fly-off arm A's exact settings (planner, xy=hold, 40 m fixed,
seed 1794000001), with `adaptive_cap.py` v2 running throughout. v2 adds a slow start (cap
doubles every 2 s until the first cut: 200 -> 1600 kbit in ~6 s, was ~52 s) and re-finds/rebuilds
the cap when a TUN is recreated. Reference: the uncapped arm-A run
`logs/campaign_20261111_010307`, first 30 min. Robots repeated their paths closely (median
gap in distance-to-UAV 3–5 m at matched elapsed time). Same metric as the fly-off summary
(time-weighted, C2 up = loss < 50 %, 10-min block bootstrap).

| | Uncapped arm A, first 30 min | **Adaptive cap** |
|---|---|---|
| Mean C2 up | 66.8 % | **91.8 %** |
| All 4 robots up | 52.0 % (CI 16–76) | **72.7 %** (CI 67–77) |
| No robot up | 17.4 % | **0.9 %** |
| Per robot a/b/c/d | 73/65/59/69 | **96/92/92/87** |
| Video latency median / p90 | 0.06 s / 73 s | 0.11 s / **1.6 s** |
| Watchdog reattaches (recovered / failed) | 13 / 2 (full 90 min) | 1 / 0 (30 min) |

* Campaign completed (rc 0); arm A confirmed in the planner log; 125 rows.
* The cap tracked moving links: caps swung between ~100 and 1600 kbit/s per robot.
* Video goodput medians are not comparable: uncapped "goodput" (1.7 Mb/s) counts a backlog
  draining at minutes of delay; the capped medians (0.70–1.55 Mb/s) are delivered at ~0.1 s.

**Caveats.** One 30-min run vs the first 30 min of one uncapped run; robot paths match closely
but channel shadowing/timing do not repeat exactly. The uncapped reattach count is for the full
90 min (research-log lines carry no timestamps). All-4-up at 73 % means individual robots
still drop; with the backlog gone those drops now track the radio.

**Implication.** The forest fly-off comparisons (both campaigns) were dominated by the UE
backlog. Re-flying them with the cap is needed before drawing any placement conclusion there.

Data: `logs/congestion_test_patrol_20261112/`, campaign `logs/campaign_20261111_234332/`.
