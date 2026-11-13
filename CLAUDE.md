# SAR nomadic 5G cell: notes for Claude Code

MSc research (Innocent Makhubela, University of Cape Town): a 5G base station
carried by a UAV serves four search-and-rescue ground robots. The stack is
OpenAirInterface (gNB + 4 UEs over the radio simulator), Open5GS 2.8.0 and a
Gazebo Harmonic world. A terrain-blind ML layer forecasts link failure and
places the UAV. Work happens on branch `ml-predictive-layer`.

## Start here

- `docs/NEW_PC_SETUP.md`: restoring the stack and data on a new PC.
- `docs/claude_memory/`: the notes kept on the lab PC (stack behaviour,
  pitfalls, results). If they are not yet in this clone's memory folder, load
  them as `docs/claude_memory/README.md` says.
- `docs/FINAL_REPEATS_INTERIM_REPORT.md`: latest results (3-seed capped runs).
- `paper/`: IEEE/IFIP NOMS 2027 paper. `paper/CLAIMS.md` holds the claims and
  the decision rules; build with `paper/pages.sh` (main text ≤ 8 pages).

## State on 13 Nov 2026

- Run data is not in git. It arrives as `sar_logs_main.tar.zst` (unpack in
  the repo root) from the backup folder, with `system/` (Open5GS settings,
  subscriber database, OAI patch and build info).
- 17 usable capped runs. Main results: the adaptive uplink cap lifts all-4-up
  by about 30 points in open field; no learned placement beats hover (open
  field: all arms within ±5 points; forest: moving arms trail by 6–10, not
  established).
- Forest seed 3 for arms B and D was flying on the lab PC when it was
  handed over; check the backup for `logs/final_repeats_rerun_*_runs.csv`.

## Next tasks, in order

1. Restore the stack on this PC and pass `verify_stack.sh` plus one short
   smoke run.
2. Retrain the forecasting models on the clean capped data
   (`scripts/ml/retrain_clean.py`, `scripts/ml/clean_dataset.py`) and fill the
   paper's retraining TODO.
3. Throughput and capacity tests (iperf, 1/2/4 robots), then the paper's
   remaining TODOs. Paper draft due 22 Nov, submission by 29 Nov 2026.
4. After submission, the realism upgrades listed in `docs/NEW_PC_SETUP.md`
   (CU/DU split, real video, fading channel, PX4, real terrain, Sionna RT).

## Rules

- **Ask before** launching any long run or campaign, and before changing OAI,
  Open5GS, MongoDB or Gazebo configuration. Back up any config first.
- **Never run two campaigns at once** on one stack. While a campaign runs, do
  not touch Open5GS/Mongo/OAI configs and do not start `nr-uesoftmodem`, even
  with `--help` (the watchdog counts modem processes).
- Launch long campaigns detached (`setsid nohup ... &`) so they survive the
  session.
- `SAR_SUDO_PASS` is in the environment for sudo
  (`echo "$SAR_SUDO_PASS" | sudo -S -p ''`). Never print or echo its value.
- **Never modify or delete original logs. Never fabricate results or labels.**
  Put new numbers in a new report file, not in README or PROGRESS claims.
- **Forbidden model inputs:** `ploss_dB`, `rsrp_computed_dBm`, any terrain
  label. `scripts/ml/dataset.py` enforces this; keep it that way.
- In the paper, never invent a number or citation; mark gaps `\todo{...}` and
  unverified references `[VERIFY]`. A confidence interval that includes 0
  means "not established".
- Commit only when asked. End commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Ask questions in prose, not with the question tool.
- A command line that contains the literal text `gz sim` matches its own
  `pgrep`/`pkill`; avoid that text in background commands.
- The GitHub repo is public: no keys, SIM credentials or backup archives in git.
