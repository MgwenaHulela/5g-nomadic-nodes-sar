# First message for Claude Code on the new PC

Clone the repo, run `git checkout ml-predictive-layer`, copy the backup folder
to `~/sar_sim_backups/20261113/`, start Claude Code in the repo, then paste
everything below the line.

---

**Context.** This is my MSc project: a UAV-borne 5G cell serving 4
search-and-rescue robots, with a terrain-blind ML layer. The cell is an OAI gNB
plus 4 UEs over rfsim, with Open5GS and Gazebo Harmonic.

- **Lab PC (where it was built):** i5-12400, 32 GB RAM, GTX 1650, Ubuntu 22.04.
- **This PC:** Intel Core Ultra with 24 cores, 32 GB RAM, and an RTX 5090-class GPU. Check the exact GPU model and memory with `nvidia-smi`.

**Step 1: get oriented.**
- Read `CLAUDE.md`, `docs/NEW_PC_SETUP.md` and `docs/FINAL_REPEATS_REPORT.md`.
- Load the notes in `docs/claude_memory/` into your memory folder for this clone, as `docs/claude_memory/README.md` says.
- Follow the rules in `CLAUDE.md` throughout:
  - Ask before installing anything, changing system or stack configuration, or launching long runs.
  - Never modify original logs.
  - Never fabricate results.

**Step 2: check this PC.** Report:
- the CPU model, how many P-cores and E-cores, and the RAM;
- the exact GPU, driver and CUDA versions;
- the Ubuntu version;
- whether OAI, Open5GS, MongoDB and Gazebo Harmonic are already installed.

RTX 50-series cards need NVIDIA driver 570 or newer and a recent CUDA. Before
anything is installed, tell me whether OAI, Open5GS 2.8 and Gazebo Harmonic all
support this Ubuntu version, or whether I should change it.

**Step 3: restore the data.**
- The backup folder from the lab PC is at `~/sar_sim_backups/20261113/`.
- Verify it with `sha256sum -c SHA256SUMS`.
- Unpack `sar_logs_main.tar.zst` into the repo root.
- Confirm the run CSVs are there: all 24 runs listed in `docs/FINAL_REPEATS_REPORT.md`.

**Step 4: plan the stack restore.** Give me a step-by-step plan. Do one step
at a time and wait for my approval at each:
1. Build OAI at commit `09c9bb998f`, without the MOCN patch.
2. Install Open5GS 2.8.0. Restore `/etc/open5gs` and the MongoDB subscribers from `system/`.
3. Install Gazebo Harmonic.
4. Pass `verify_stack.sh` and one short smoke run.

For this CPU, plan to pin the gNB's rfsim radio thread to its own P-core and
keep everything else off that core.

**Step 5, after the stack works: the realism upgrades, before the paper.** Do
them in this order. Ask me before each one, and make each a separate, backed-up
change with one validation run against the restored lab-PC setup:

1. A CU/DU split over F1, with delay added on F1 (DU on the UAV, CU on the ground).
2. Real video traffic: recorded footage, variable bit rate, NVENC encoding on the GPU.
3. A fading channel from rfsim's 3GPP models. Check the radio thread's CPU load with 1, 2 and 4 robots.
4. PX4 SITL for real drone dynamics (wind, battery), using the existing x500 model.
5. A world built from real terrain and buildings for one real South African site, plus ray-traced propagation with NVIDIA Sionna RT on the GPU, feeding the channel bridge.
6. Optionally, 2–3 simulation stacks in parallel. RAM is the limit here: each stack needs about 5 GB, and the stacks must be isolated from each other.

Label runs from the upgraded stack as a new setup. Never pool them with the 24
lab-PC runs.

**Step 6, then the NOMS 2027 paper** (draft by 22 Nov, submit by 29 Nov):
1. Retrain all model classes on the clean capped data (`scripts/ml/retrain_clean.py`), and fill the paper's retraining TODO.
2. Run the throughput and capacity tests (iperf, 1/2/4 robots), and fill those TODOs.
3. Rebuild with `paper/pages.sh`. The main text must be at most 8 pages, with no undefined references.

Tell me plainly if the upgrades are putting these dates at risk.

Start with Steps 1 to 3 now, then stop and report before installing anything.
