# Moving the SAR simulation to a new PC

Written 13 Nov 2026, when the work moved from the lab PC (i5-12400, 12 threads,
32 GB, GTX 1650 4 GB) to a more powerful machine.

## What lives where

| What | Where it is | How it moves |
|---|---|---|
| Code, paper, analysis scripts, reports | This repo, branch `ml-predictive-layer` | `git clone`, then `git checkout ml-predictive-layer` |
| Run data (`logs/`, about 16 GB raw) | Ignored by git | `sar_logs_main.tar.zst` (all runs and ML outputs) and `sar_logs_big_raw.tar.zst` (two 5.9 GB fast-radio files from 21 Oct plus the 1.3 GB channel-bridge log) |
| Open5GS settings (`/etc/open5gs`) | Outside the repo | `system/etc_open5gs.tar` |
| Robot subscriber records (MongoDB `open5gs`) | Outside the repo | `system/mongodump/` |
| OAI local changes and build commit | `~/openairinterface5g` | `system/oai_local_changes.patch`, `system/oai_info.txt` |

The backup folder is `~/sar_sim_backups/20261113/` on the lab PC. Copy it by
USB drive or your own cloud storage. It holds test SIM keys and Open5GS TLS
and home-network keys, so do not put it in the public repo.

Check the copy on the new PC with `sha256sum -c SHA256SUMS`.

## Restore order

1. **OS and tools.** The lab PC runs Ubuntu 22.04 (kernel 6.8), Gazebo Harmonic
   (`gz sim` 8.14), Python 3.10 and Open5GS 2.8.0 (systemd services).
   `install_requirements.sh` covers the Python side.
2. **OAI.** Clone OpenAirInterface. The running binaries were built from commit
   `09c9bb998f`. The source tree's HEAD was later moved to `62fafd5860` for
   the MOCN experiment, and `oai_local_changes.patch` holds uncommitted MOCN
   edits. For the SAR results, build `09c9bb998f` without the patch, then build
   `nr-softmodem` and `nr-uesoftmodem` with the radio simulator.
3. **Open5GS.** Install 2.8.0, then restore `/etc/open5gs` from
   `etc_open5gs.tar` and the subscribers with
   `mongorestore --db open5gs system/mongodump/open5gs`. The SAR runs use the
   `internet` DNN on 10.45.0.0/16 via `ogstun`. `smf2`, `upf2`, `ogstun2` and
   10.46/16 are leftovers of the MOCN experiment.
4. **Data.** Unpack the archives in the repo root:
   `zstd -dc sar_logs_main.tar.zst | tar -xf -`.
5. **Check the stack** with `verify_stack.sh`, then one short smoke run before
   any campaign. Read `docs/WORLDS_RUNBOOK.md` and the silent-failure checks
   first: a run with `ploss_dB == 0`, a missing TUN in its namespace, or fewer
   than four UEs is void.

## Rules that carry over

- Never run two campaigns on one stack at once. `run_research_campaign.sh`
  holds a lock for this.
- Launch long campaigns detached (`setsid nohup ... &`) so that closing a
  terminal or an assistant session cannot stop them.
- About 4 % of stack start-ups fail because Gazebo takes longer than the
  15-s wait in `start_full_stack.sh`. The runner flags them NOT COMPARABLE.
  Lengthening that wait is a candidate fix.
- Free memory before long runs. The lab PC's campaign was stopped once when
  browsers, the stack and a growing `open5gs-amfd` (1.4 GB after 27 days)
  filled 32 GB.

## Using all the CPU cores

One stack does not use many cores. The OAI radio simulator's radio thread uses
about one full core and was the suspected limit on cell capacity, and Gazebo,
the bridge and the four UEs need a few more. So there are three ways to use a
large CPU:

1. **Faster single stack.** Give the gNB's radio thread a dedicated core that
   nothing else uses, and check the softmodem's own help for its thread-pool
   and core-pinning options. This should lift the capacity limit, not add runs.
2. **Several stacks in parallel.** This is where many cores pay off: N
   independent campaigns at once, for example one per seed. It needs each
   stack isolated (its own containers or network namespaces, Open5GS
   instance, ports, `/tmp` files and Gazebo partition), because today's
   scripts assume one stack per machine. This is a real engineering task, not
   a setting.
3. **Analysis and training.** The ML scripts use scikit-learn and LightGBM
   (`n_jobs=-1` uses every core) and PyTorch (uses the GPU when CUDA is
   available).

## Planned realism upgrades (before the paper; user decision 13 Nov 2026)

In this order, each with one validation run against the current setup:

1. CU/DU split over F1 (OAI ships `gnb-cu.sa.f1.conf` and
   `gnb-du.sa.band78.106prb.rfsim.pci0.conf`), with delay added on F1.
2. Real video traffic (recorded footage, variable bit rate) instead of 1.5 Mb/s
   constant.
3. A fading channel from the radio simulator's 3GPP channel models. Check CPU
   load with 1, 2 and 4 robots first.
4. PX4 SITL for real drone dynamics (the x500 model is already in use).
5. A world built from real terrain and buildings for one real site.
6. Ray-traced propagation for that site with NVIDIA Sionna RT on the GPU.

The data and modelling questions for outside ML help are written up in the
"SAR Link Forecasting Brief" page.
