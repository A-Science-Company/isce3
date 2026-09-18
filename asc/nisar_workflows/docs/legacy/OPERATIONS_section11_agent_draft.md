# Superseded draft of OPERATIONS_AND_LESSONS.md section 11 (hand-written by an agent, 2026-09-14)

Replaced on 2026-09-15 by the generated log (tools/render_error_log.py --doc OPS).

## 11. Problems and errors log

Status vocabulary: **fixed** (in code or on the machine, verified by reading), **fixed in code, unrun**
(v2 tools), **worked around**, **open**, **by design**. "Assistant error" means a mistake of judgement
or execution by the AI assistant, as opposed to a tool defect or external event.

### 11.1 Summary

| ID | Title | Category | Caught by | Assistant error? | Status | Cost |
|---|---|---|---|---|---|---|
| OPS-01 | `pkill -f`/`pgrep -f` self-match killed command chains (9 occurrences incl. Sep 14) | process control | assistant | yes | open | lost edits/launches, disk guard disabled during a ~24 h run; class also caused the ~4 h gslcB deadlock (G_full) |
| OPS-02 | `python ... | tee log; echo EXIT=$?` reported EXIT=0 for a failed run | exit codes | assistant, after user query | yes | fixed | contributed to ~3 h undetected failure |
| OPS-03 | Agent-session watchers died on client restart; OOM unnoticed ~3 h | monitoring | user | yes | worked around | ~3 h |
| OPS-04 | ~24 h run launched with nohup under `Linger=no`; tmux + linger only after user raised it | supervision | user | yes | fixed | restart; 3 m 14 s run discarded |
| OPS-05 | Monitor hygiene: `esample` filter flood, timeouts, duplicate watcher, blank capture, stale tmux sessions | monitoring | tool (rate limiter) / assistant | yes | worked around | notification spam; permanently incomplete `iono_chain.log` |
| OPS-06 | No completion watcher, stages not chained: G_crop sat idle 68 min until the user asked | orchestration | user | yes | worked around | 68 min idle, two user prompts |
| OPS-07 | No discoverable progress log: journal in a separate file, stale logs side by side | logging | user | no | worked around | user confusion, repeated status requests |
| OPS-08 | Block-buffered stdout froze and truncated logs; `python -u` does not reach ISCE3 children | logging | assistant | yes | worked around (partial) | misleading progress; unrecoverable log tail on Sep 8 |
| OPS-09 | VM too small for freq A at 1x1 (3.9 GB, 2 cores); disk was not the limit | capacity | crash | no | fixed (resize) | 27 h 24 m run lost, ~22 h until resize |
| OPS-10 | 8 GiB swapfile silently inactive since the resize reboot | capacity | this review | no | open | no swap buffer on every later run |
| OPS-11 | Disk increases not usable until partition and filesystem grown | capacity | user | no | fixed | minutes each time |
| OPS-12 | Unplanned VM stops/reboots killed tmux sessions | host | assistant (on resume) | no (event); see G_full for the misreport | worked around | truncated GSLC (G_full); lost `/tmp` (OPS-14) |
| OPS-13 | USER CAUGHT: gdalwarp running after "nothing depends on a live process"; power-off advised; linger said to survive reboots | supervision | user | yes | fixed | ~10 min warp lost; false resume guidance |
| OPS-14 | `/tmp` (tmpfs) losses: wrapper scripts, stack copy, warp output; orphan GDAL work files in the case dir | persistence | user (via OPS-13) | yes | worked around | reproducibility of W3/W4 launches; 36 MB orphans |
| OPS-15 | Agent client interruptions: OAuth expiry, session limit, VSCode synthetic resumes, auto-compaction | agent harness | tool | no | worked around | 13 min user time; 2 h 10 m reporting latency; lost context |
| OPS-16 | Harness friction: sleep polling blocked; workflow script parse error | agent harness | tool | yes | fixed | ~2 min |
| OPS-17 | `verify.sh` environment-check bugs exposed by the Docker build | environment | tool (Docker build) | yes (earlier session) | fixed | failed image build step |
| OPS-18 | Overlay tooling gaps: pre-patch identity not checked; `--check` passes unapplied patches; mask test vacuous after overlay; sources uncommitted | environment | this review | yes | open | latent: stock env passes verify.sh |
| OPS-19 | Driver `--dry-run` writes a log file despite "without running or writing anything" | tooling | this review | yes | open | latent: dry runs are not side-effect free |
| OPS-20 | `STATE.md` resume document carried withdrawn claims | state/docs | verification audit | yes | fixed (rewritten 15:15) | four wrong facts available to any resumer for ~4.5 h |
| TOOL-01 | Overlay requested in VV; granules are DHDH (HH+HV) | data/request | assistant | no | by design | none |
| TOOL-02 | Publishing bucket enforces Public Access Prevention | delivery | assistant | no | by design | no live URL |
| TOOL-03 | GDAL geolocation warp with auto bounds clipped the swath | GDAL | assistant | no | fixed | ~16 min from detection to rebuilt output |
| TOOL-04 | GDAL-Python dangling dataset `TypeError` (three occurrences) | GDAL/Python | crash | yes | fixed | ~2 min each |
| TOOL-05 | -134 dB sinc-ringing fill broke the dB stretch; `_work` deleted by default | QA tooling | assistant | yes | fixed | 8 m 32 s rebuild |
| TOOL-06 | USER CAUGHT: layer switcher hidden under the panel; "verified" by grepping the HTML | QA tooling | user | yes | fixed | ~5-10 min rework, credibility |
| TOOL-07 | GDAL geolocation arrays assume corner coordinates; rdr2geo lon/lat are centres | GDAL | assistant | no | fixed | none |
| TOOL-08 | PIXEL_CENTER fix not ported to `slc_amp_overlay.py` | GDAL | verification audit | yes | open | overlay ~half a look cell off the basemap |
| TOOL-09 | `SRS: EPSG:4326` in GEOLOCATION metadata -> `ERROR 1: missing [` | GDAL | tool (error line) | no | fixed | negligible |
| TOOL-10 | Empty warp blamed on relative `X_DATASET` paths; the check window was outside coverage | diagnosis | verification audit | yes | fixed (claim withdrawn) | ~4 min plus a false trap in docs |
| TOOL-11 | Shadow/layover: GDAL backmap fills holes with distant radar pixels (max 8.95 km) | GDAL | assistant | no | fixed | extra run; 7.14% of AOI excluded |
| TOOL-12 | Geolocation lookup warp took 1549 s; stall suspected, clock misread | GDAL/perf | assistant | no | worked around (cached) | ~26 min once |
| CMP-01 | Loop variable shadowed `b = args.buffer`; crash after 30.7 min, all results in memory | comparison tooling | crash | yes | fixed (rename); end-only write open | 30.7 min, ~4 min with caches |
| CMP-02 | Frame-shift prediction for offsets wrong; offsets in metres labelled pixels | comparison tooling | assistant / critic | yes | fixed in code, unrun | negligible; mislabelled C1 numbers |
| CMP-03 | `screen_agreement` a/b order inverted; opposite sign conventions in one JSON | comparison tooling | verifier V4 / critic | yes | fixed in code, unrun | mislabelled values reached verification |
| CMP-04 | `nearest_cycle_combo` attribution meaningless | comparison tooling | verifier V1 | yes | fixed in code, unrun | supported a wrong narrative |
| CMP-05 | NaN nodata with `!= 0` masks -> all-NaN histogram | comparison tooling | verification audit | yes | open (report_figures) | one empty chart; mask fraction overstated |
| CMP-06 | Caches keyed by file existence; comparison wrote into a workflow product dir | comparison tooling | verification audit | yes | fixed in code, unrun | latent stale-layer mixing |
| CMP-07 | Case-specific constants hard-coded | comparison tooling | verification audit | no | open | porting needs edits |
| CMP-08 | Nearest-neighbour geocoding caps 5 m cross-geometry agreement | comparison method | verifier V2 | no | by design | 5 m number is a bound, not agreement |
| CMP-09 | Cause-laden naming: "planar ramp" in code comments and figure names | comparison tooling | verifier V2 | yes | fixed in compare v2; open in report_figures | risk of misinforming the report |

### 11.2 Entries

#### OPS-01 - `pkill -f` / `pgrep -f` self-match killed command chains
- **Symptom.** Tool calls returned `Exit code 144` with no output at T:L514, L517 (Sep 3 18:33),
  L1043 (Sep 4 01:23), L1405 (Sep 4 12:48; the background "Guard against disk exhaustion during freq A run"
  also failed with exit code 144, T:L1404), L1944 (Sep 6 19:03), L2355 (Sep 7 04:17), L2462 (Sep 7 05:14:
  "the crossmul_path insertion didn't land and the tmux session never started", T:L2468), L2992 (Sep 8 09:29,
  the G_full 8x8 -> 1x1 restart; config edit redone), and **L4924 (Sep 14 14:59:46)**: the command
  `tmux kill-session -t aligned; ... pkill -f "configs/nepal_glof_aoi_aligned.yaml"; ... mv aoi_aligned ...`
  (T:L4923) died, so the move of the aborted outputs never ran; it was redone in a separate call at
  15:00:00 (T:L4935) after checking with `pgrep -af "[n]epal_glof_aoi_aligned"` (T:L4928).
- **Root cause.** The harness runs each call as `bash -c` with the whole command in argv (T:L4584).
  `pkill -f`/`pgrep -f` match full command lines, so the pattern matches the invoking shell (and any
  watcher that quotes it). The disk guard's own `pgrep -f "nisar.workflows.insar"` loop (T:L1373)
  could never observe the process exit. The same class produced the gslcB waiter deadlock
  (`while pgrep -f 'run_track_g.py --config configs/nepal_glof.yaml --only gridgate igram'`,
  T:L3216; home: G_full document).
- **Fix.** None systematic. Later waits use `tmux has-session` (T:L3725, T:L4623); the Sep 14
  recurrence shows the lesson was not applied consistently.
- **Cost.** Minutes per occurrence; a silently disabled disk guard from Sep 4 12:48; lost edits and
  a lost tmux launch that had to be rediscovered.
- **Evidence.** Transcript lines above; `V:errors_W5_ops_and_comparison.json`, `V:errors_W1_rslc_full.json` entry 4.
- **Automation lesson.** Never locate processes by command-line pattern. Record PIDs at launch, run
  each stage as a named tmux session or systemd unit, check post-conditions in a separate step, and
  never chain work after a kill in the same shell.

#### OPS-02 - `| tee log; echo EXIT=$?` reported success for a failed run
- **Symptom.** The tmux console showed `ERROR: RUN FAILED at step 4 insar` followed by `EXIT=0` (user-pasted, T:L1582).
- **Root cause.** `$?` after a pipeline is the last command's status (`tee`), not python's (T:L1585).
- **Fix.** `set -o pipefail` and `${PIPESTATUS[0]}` in later wrappers (`C/logs/run_compare.sh:2,9`, `run_aoi_v2.sh:4,19`).
- **Cost.** Contributed to the user believing the 27 h run had finished.
- **Evidence.** T:L1582, T:L1585; `V:errors_W1_rslc_full.json` entry 20.
- **Automation lesson.** The worker's exit code goes to a status file from `wait`/`PIPESTATUS`; success is never read off a console tail.

#### OPS-03 - Agent-session watchers died; OOM unnoticed for ~3 h
- **Symptom.** OOM kill Sep 5 16:14:26 (`C/logs/track_r_20260904T124949Z.log:1094` "failed with return code -9 after 27h24m"). Nothing alerted; the user asked "dude so the run is done ,can you cehck once" at 19:10 (T:L1541).
- **Root cause.** The stage watcher and resource guard were Claude background tasks; the client session restarted overnight (T:L1465) and they died with it (T:L1631).
- **Fix.** Watchers re-armed by hand on each return; the assistant later warned that its monitors die with the session. Detection is still agent-bound (Section 10 item 1).
- **Cost.** ~3 h before anyone knew; the user was misled about completion.
- **Evidence.** T:L1465, L1541, L1631; `V:errors_W1_rslc_full.json` entry 19. (OOM mechanism: R_full document.)
- **Automation lesson.** Failure detection lives with the job (wrapper status file + supervisor hook or cron checker that notifies), never in an interactive session.

#### OPS-04 - Long run launched with nohup under `Linger=no`
- **Symptom.** At 12:45:52 the assistant reported the freq-A run "detached (pid 38019) so it survives session interruptions" (T:L1376). At 12:46:54 the user asked about tmux (T:L1379). Checking showed `session-32.scope` and `Linger=no` (T:L1390).
- **Root cause.** Reparenting to PID 1 was assumed sufficient; lingering was not checked before claiming survivability. `KillUserProcesses=false` meant logout would not kill it directly, but the user slice could still be torn down (T:L1438).
- **Fix.** `loginctl enable-linger sharath` -> `Linger=yes` (T:L1426-L1427) and relaunch in tmux; parentage verified (T:L2100); disconnects on Sep 7 survived (T:L2647).
- **Cost.** The restart discarded a run that had reached 3 m 14 s (`C/logs/track_r_20260904T124508Z.log:39`), shared with the block-size fix.
- **Evidence.** as cited; `V:errors_W1_rslc_full.json` entry 15.
- **Automation lesson.** Launch long stages only under a supervisor, check `Linger=yes` first, and print attach/log commands at launch.

#### OPS-05 - Monitor hygiene failures
- **Symptom.** (a) Sep 3 20:41: dozens of "Monitor event: Track R stage transitions and failures" notifications, then "[1 events suppressed - output rate too high ...]" (T:L736-L747). (b) "[Monitor timed out - re-arm if needed.]" (T:L497). (c) A duplicate watcher on `unwrap_1x1.log` (T:L2525). (d) Log-capture loop writing blank lines (T:L2843); `C/logs/iono_chain.log` is 837 B and holds only Topo lines, never the ionosphere stage or `CHAIN_EXIT`. (e) Four stale tmux sessions sleeping on finished work (T:L2850).
- **Root cause.** Ad hoc `tail | grep` and `capture-pane` monitors with loose patterns (`esample` matched every `resample_slc_blocks` journal line), no ownership or cleanup, and a fixed monitor timeout.
- **Fix.** Anchored patterns (`Successfully ran|Traceback|MemoryError|Killed|No space left`), duplicate and stale sessions killed, pane-diff capture.
- **Cost.** Notification noise with a real risk of suppressed failure events; an incomplete log.
- **Evidence.** as cited; `V:errors_W1_rslc_full.json` entry 5; `V:errors_W5`.
- **Automation lesson.** Stages write structured markers (STAGE_START/STAGE_END/EXIT) to a status file; monitors watch those, are created and destroyed by the orchestrator, and never parse free-text ISCE3 logs for control flow.

#### OPS-06 - No completion watcher, stages not chained: 68 min idle
- **Symptom.** The G_crop GSLC + gridgate wrapper finished with `gridgate EXIT=1` at 09:11:31. Nothing happened until the user asked "how is the run going ?" at 10:19:41 (T:L4289 -> T:L4292; T:L4299, T:L4307). The next wrapper was again launched without a watcher; the user asked again at 10:30 (T:L4348), 3 min after it finished.
- **Root cause.** The assistant's turn ended at 08:58:36 with a promise to continue but no background waiter, and the wrapper (`run_aoi_gslc.sh`, lost from `/tmp`) did not chain igram or ionosphere. The deferred freqAB resolver fix made gridgate fail, but the idle would have occurred even on success (`V:critic.json` contradiction 10).
- **Fix.** None in session beyond resuming by hand. Later wrappers chain stages (`run_aoi_v2.sh`).
- **Cost.** 68 min wall-clock idle; two user status prompts.
- **Evidence.** as cited; `V:errors_W4_gslc_crop.json` entry 2 (workflow details, including the resolver bug, in the G_crop document).
- **Automation lesson.** The orchestrator owns sequencing; every DAG edge is an explicit dependency with exit-status propagation, and success and failure both notify.

#### OPS-07 - No discoverable progress log
- **Symptom.** The assistant reconstructed stage timing from file mtimes ("The journal bypasses the console log", T:L1499). The user asked for a `tail -f`-able log (T:L2650). The wrapper log the user might open, `trackA_runw_console.log`, belonged to the earlier failed 8 h 34 m run; the live one was `unwrap_1x1.log` (T:L2660).
- **Root cause.** ISCE3's journal writes to the runconfig's `logging.path` (`SP/nisar/workflows/runconfig.py:94-103`), not the driver console; log names did not identify the live run.
- **Fix.** User pointed at the right log with tile-count one-liners; later wrappers write `=== <UTC> <step> EXIT= ===` markers.
- **Cost.** User confusion and repeated status requests.
- **Evidence.** as cited; `V:errors_W5`.
- **Automation lesson.** One run-ID directory per run with a single progress file (stage, block i/N, ETA, log paths) printed at launch; superseded logs archived or suffixed.

#### OPS-08 - Block-buffered stdout
- **Symptom.** The GSLC ionosphere v1 log showed only its START line for over an hour (T:L3857); the gslcB driver log ends with a burst of identical timestamps at 18:48:58.61x, so the last real activity before the Sep 8 stop is unknown (G_full document).
- **Root cause.** Python block-buffers stdout when it is not a TTY. `python -u` was added to wrappers later, but it does not propagate to the ISCE3 child interpreters launched by `run_cmd` with the inherited environment (`R/nisar_wf/util.py:273-283`, `trackr.py:691-695`, `gslc.py:455-459`; found in this review).
- **Fix.** `python -u` in wrappers (`run_compare.sh:8`, `run_report_figures.sh:8`, `run_aoi_v2.sh`); `PYTHONUNBUFFERED` not set anywhere.
- **Cost.** Misleading progress; a truncated log tail that fed the Sep 8 completeness misdiagnosis.
- **Evidence.** as cited; `V:errors_W2_gslc_full.json` entry 22.
- **Automation lesson.** Export `PYTHONUNBUFFERED=1` in the pipeline environment; judge completion from exit-status files and product validation, never from log tails.

#### OPS-09 - VM too small for freq A at 1x1
- **Symptom.** Kernel: `Out of memory: Killed process 38290 (python) total-vm:12324044kB, anon-rss:3548012kB` at Sep 5 16:14:26 (T:L1567), with 218 GiB free disk (T:L1639). The user asked "okay wait if I increase storage can we run ?" (T:L1634); the limit was RAM.
- **Root cause.** The VM (3919 MiB RAM, 2 cores, 8 GiB swap) had been sized for the original ~3 GB constraint; the full-swath mask reads on freq A need several GB independent of looks (mechanism and the corrected ~8.7 GB transient peak: R_full document). The "0.4 GB short" figure is withdrawn (`V:critic.json`).
- **Fix.** User resized the VM to 31 GB RAM and 8 cores on Sep 6 (T:L1682).
- **Cost.** 27 h 24 m of compute lost, then ~22 h until the resize.
- **Evidence.** as cited; `V:errors_W5`; `V:errors_W1_rslc_full.json` entry 18.
- **Automation lesson.** Pre-flight a per-stage memory estimate from grid sizes (including non-looks-dependent reads) and refuse to start, naming the stage that would fail.

#### OPS-10 - Swapfile silently inactive after the resize reboot (found in this review)
- **Symptom.** On the 3.9 GB box `/proc/swaps` listed `/swapfile` (T:L1650). From Sep 6 onward `SwapTotal 0` (T:L2427); measured here `/proc/swaps` is empty while `/swapfile` (8 GiB, Sep 3 16:48) still exists.
- **Root cause.** The swapfile was activated without an `/etc/fstab` entry (measured: fstab has only `/` and `/boot/efi`), so the Sep 6 reboot dropped it. Nobody recorded the change.
- **Fix.** None. Whether swap is wanted is an explicit decision to make (Section 10 item 3).
- **Cost.** No incident attributable; every later RAM estimate was made against zero swap with `overcommit_memory=1`.
- **Evidence.** as cited; commands in Section 2.1.
- **Automation lesson.** Record host memory configuration (RAM, swap, overcommit, tmpfs sizes) in the run manifest and compare it with the previous run; a changed host is a gate event.

#### OPS-11 - Disk grown but not usable
- **Symptom.** "I increased storage from 500GB to 600GB but we need to make it available" (T:L1663); sda1 still 499.9 GB (T:L1682). At 1000 GB sda1 still ended at 751 GB and the user had to add "but we need to make the hdd available" (T:L3456).
- **Root cause.** Cloud disk resize grows only the block device.
- **Fix.** `sudo growpart /dev/sda 1` and `sudo resize2fs /dev/sda1`, online: 492G/218G free -> 591G/312G free (T:L1687); 700 GB grown by the user (T:L2611); 92 -> 375 GB free (T:L3462).
- **Cost.** Minutes each time; disk pressure drove the scratch deletion recorded in the R_full document.
- **Evidence.** as cited; `V:errors_W1_rslc_full.json` entry 40.
- **Automation lesson.** The disk gate compares disk, partition and filesystem sizes and either grows them (with sudo) or emits the exact commands.

#### OPS-12 - Unplanned VM stops and reboots
- **Symptom.** Boot -3 ended Sep 8 18:53:22 during a GSLC finalisation; boots on Sep 11 06:17 and Sep 14 02:44 had no recorded cause; on resume "The box rebooted 23 min ago (Sep 14 02:44) - that's what killed tmux" (T:L3417).
- **Root cause.** External events (cause unrecorded). Nothing in the pipeline records boot identity, so the Sep 8 stop was misread on Sep 14 as a harmless reboot after completion (G_full document).
- **Fix.** Work resumed from disk; the truncated product was found only by the later audit.
- **Cost.** One incomplete L2 product presented as complete (G_full); `/tmp` contents lost at 12:52 (OPS-14).
- **Evidence.** `journalctl --list-boots` (Section 2.2); `V:critic.json` contradiction 8.
- **Automation lesson.** Assume reboots. Store boot id per stage; any stage whose START and END boot ids differ, or that has no END, is re-validated and restarted from on-disk state.

#### OPS-13 - USER CAUGHT: process still running after "nothing depends on a live process"
- **Symptom.** At 10:44:07 the assistant wrote "Everything is on disk; nothing is held in memory or depends on a live process" and "the remaining step is ~5 minutes" (T:L4442), the same claim written into STATE.md. At 10:44:49 the user asked "I still see gdalwarp running, is the run still going on ? or can I switch off the vm ?" (T:L4445). The warp had run 6 m 12 s with 0 bytes written (T:L4455). The assistant advised switching off and added that `loginctl enable-linger` "is set for your user, so it survives reboots" (T:L4457). After restart: "up 1 minute", scratchpad "total 0" (T:L4484).
- **Root cause.** The warp had been started with `nohup ... &` from the agent shell into the tmpfs scratchpad (T:L4405); GDAL writes nothing until the end; the assistant did not list live processes before declaring a safe stop point, and conflated linger (logout) with reboot survival.
- **Fix.** Ad-hoc path abandoned; `tools/compare_four_way.py` writes under the case directory and caches the lookup (v1 docstring; v2 `compare_four_way.py:7-10`).
- **Cost.** ~10 min of warp lost; a STATE.md resume instruction pointing at a deleted `/tmp` file; a false statement about linger that was not corrected to the user.
- **Evidence.** as cited; `V:errors_W5`; `V:errors_W3_rslc_crop.json` entry 26.
- **Automation lesson.** "Safe to power off" is a computed answer: list live stage PIDs, their output locations and persistence, then answer. Linger is not reboot survival.

#### OPS-14 - `/tmp` (tmpfs) losses and orphan work files
- **Symptom.** The 12:52 reboot emptied the session scratchpad (T:L4484). Lost, by workflow (write events from the transcript; none exist under `/home/sharath/isce3` today, `find` measured here):
  - G_full: `run_igram_8x8.sh` (T:L3540), `run_gslc_iono.sh` (T:L3748), `planned.txt` (T:L3526).
  - R_crop: `run_subset.sh` (T:L4058), `run_aoi_trackr.sh` (T:L4182), `run_aoi_insar.sh` (T:L4213).
  - G_crop: `run_aoi_gslc.sh` (T:L4267), `run_aoi_igram.sh` (T:L4338), `stack_A_only.json` (T:L4239).
  - Comparison: `rifg_crop.vrt`, `rifg_crop_geo.tif`, `warp.log` (T:L4386, T:L4405).
  - Earlier boots also discarded scratchpad logs of the first Track R runs and overlay builds (`trackr_pair1.log` T:L392, `overlay*.log` T:L949-L1091, `trackr_freqA.log` T:L1362).
  Separately, three GDAL FillNodata working files sit in `C/` with mtime Sep 14 10:45 (Section 7.3).
- **Root cause.** Scripts, manifests and intermediates were written to a session-private tmpfs directory; GDAL was run with the case directory as CWD and killed by the power-off before cleaning its temp files (inference from names, CWD and timestamp; PID 70284 was not recorded).
- **Fix.** Since 13:08 Sep 14, wrappers are written to `C/logs/`. The lost scripts were not recreated; their flags survive only in the transcript (and, for Track G stages, in the driver logs' echoed arguments).
- **Cost.** Loss of the exact launch commands for R_crop v1 and G_crop v1, including the ionosphere invocation; 36 MB of orphans.
- **Evidence.** as cited; `V:critic.json` misattributed item on `/tmp` loss; `V:errors_W4_gslc_crop.json` entry 5.
- **Automation lesson.** Nothing the pipeline needs to reproduce or resume may live on tmpfs. Run GDAL with a scratch CWD. Gate G6.

#### OPS-15 - Agent client interruptions
- **Symptom.** (a) Sep 14 02:51: synthetic reply "Failed to authenticate: OAuth session expired and could not be refreshed" (T:L3365); the user re-sent after 13 min. (b) Auto-compaction at 03:07:51, `preTokens 969482` (T:L3383); the crop-feasibility finding was lost (R_crop document). (c) "You've hit your session limit · resets 8am (UTC)" at 05:36 and 05:53 (T:L3954, T:L3958) while the GSLC ionosphere v3 solve ran; it finished at 05:53:40 without the agent (`C/logs/trackG_ionosphere_v3.log`) and was reported at 08:03 (T:L3974). (d) VSCode crash: "Continue from where you left off." followed by synthetic "No response requested." (T:L4511-L4513, and Sep 8 T:L3024-L3025); user: "hey vscode crashed, thats weird as we are in vm".
- **Root cause.** Agent session, quota and client limits, independent of the pipeline.
- **Fix.** Runs in tmux with linger continued; state re-read from disk on resume (T:L4518).
- **Cost.** 13 min of user time; 2 h 10 m reporting latency; detail lost in compaction.
- **Evidence.** as cited; `V:errors_W2_gslc_full.json` entry 11.
- **Automation lesson.** The pipeline must be complete without the agent: detached execution, self-reporting to disk, and a resume command that prints status from the manifest.

#### OPS-16 - Harness friction
- **Symptom.** "Blocked: sleep 60 followed by: tail -4 ... To wait for a condition, use Monitor with an until-loop" (T:L4576; also T:L399, T:L3164, T:L3234). The verification workflow first failed with "Invalid workflow script: Script parse error: Unexpected token (123:91)" on an escaped apostrophe (T:L4686).
- **Root cause.** Harness policy; a quoting mistake in generated JavaScript.
- **Fix.** Background until-loops; corrected script.
- **Cost.** ~2 min.
- **Evidence.** as cited; `V:errors_W5`.
- **Automation lesson.** Expose a blocking wait-for-stage primitive (status files) so no agent needs ad-hoc polling.

#### OPS-17 - `verify.sh` environment-check bugs (commit 9f69cf06)
- **Symptom.** The NISAR Docker image build failed at the verify step.
- **Root cause.** Three bugs in `asc/env/verify.sh` (commit message): `conda run -n isce3_env bash -lc` re-activated `base` in a login shell, so every CLI reported MISSING; `dolphin_env` was treated as mandatory; `conda run ... | head -5` under `pipefail` exited 120 on SIGPIPE, racily.
- **Fix.** `bash -c`; `dolphin_env` optional (reported "NOT PRESENT -- skipping"); capture-then-trim (`asc/env/verify.sh:76-115`).
- **Cost.** A failed image build step; pre-dates the Nepal runs (Sep 3 15:07 UTC).
- **Evidence.** `git show 9f69cf06`; `V:critic.json` missing problem "verify.sh environment-check bugs".
- **Automation lesson.** Environment checks run in the same shell mode as the pipeline (non-login), treat optional components explicitly, and never pipe a producer into `head` under `pipefail`.

#### OPS-18 - Overlay tooling gaps (found in this review)
- **Symptom.** `apply_patches.py --check` reports all four overlays applied, but: the documented pre-patch byte-identity check (`apply_patches.py:15-22`) is not in the code (`:166-197`); `--check` leaves `rc = 0` for a patch that is NOT applied (`:189-192`), so `verify.sh` would pass a stock environment; `tools/test_insar_mask_patch.py:18` imports the "stock" mask function from the now-patched module and tests `patches/insar_mask_vectorized.py` rather than the installed `patches/insar_utils.py`; three patch sources and the `PATCHES` extension are uncommitted (`git status`).
- **Root cause.** The overlay mechanism grew from one committed patch (Sep 3) to four (Sep 6-7) without extending its checks or committing the sources.
- **Fix.** None.
- **Cost.** Latent: a fresh VM built from the repository would miss three overlays that the freq-A 1x1 path needs, and verification would say it passed.
- **Evidence.** as cited; `cmp` results and function diff in Sections 2.4 and 7.1; `V:critic.json` missing problem on benchmark provenance (home: R_full).
- **Automation lesson.** Overlays are versioned artifacts with expected pre- and post-patch hashes; the check fails when any required overlay is missing; the active overlay set is written into every product's provenance.

#### OPS-19 - `--dry-run` writes a log (found in this review)
- **Symptom.** Help text says "report what each step would do ... without running or writing anything" (`R/run_track_r.py:188-190`; Track G equivalent in `--help`).
- **Root cause.** Both drivers create `<root>/logs/` and construct `Logger(log_path)` in dry-run mode (`run_track_r.py:294-298`, `run_track_g.py:334-339`), and `Logger.__init__` appends a header to the file (`R/nisar_wf/util.py:111-115`). (The earlier dry-run failure on a missing runconfig is in the R_full document.)
- **Fix.** None.
- **Cost.** Latent; a "read-only" dry run changes the log directory and can create `out_root` trees for a new config.
- **Evidence.** as cited.
- **Automation lesson.** A dry-run mode must be verifiably side-effect free (run it under a read-only mount or check `find -newer` before and after), or be documented as writing logs.

#### OPS-20 - `STATE.md` carried withdrawn claims
- **Symptom.** The 10:45 STATE.md asserted "Nothing is held in memory, no process needs to stay alive", "ALL FOUR COMPLETE", "RSLC full -1.4390 / GSLC full -1.4284 <- agrees to 0.7%", the whole-scene-vs-AOI degeneracy explanation, the relative-`X_DATASET` trap, and a resume path into `/tmp` (`V:critic.json` stale list).
- **Root cause.** Written by hand mid-analysis; at 13:56 the assistant deliberately deferred correcting it until verification finished ("I'm deliberately not touching STATE.md yet", T:L4702).
- **Fix.** Rewritten at 15:15 with a "Withdrawn or refuted claims - do not re-introduce" list (`R/STATE.md:89-105`).
- **Cost.** For ~4.5 h any resumer (human or automation) would have inherited wrong facts.
- **Evidence.** as cited; `V:errors_W5` (STATE.md entry).
- **Automation lesson.** Generate state documents from manifests and comparison outputs; stamp each claim with its source artifact so a retraction propagates.

#### TOOL-01 - Overlay requested in VV; granules are DHDH
- **Symptom.** "can we geocode the coregistered slcs vv ampitude" (T:L893); no VV in any granule (T:L908).
- **Root cause.** Data: dual-pol HH+HV on both frequencies.
- **Fix.** Built with HH, labelled HH everywhere, with a visible note; the user confirmed "my bad it is HH band and not VV" (T:L1143).
- **Cost.** None. **Evidence.** as cited; `V:errors_W1_rslc_full.json` entry 7.
- **Automation lesson.** Validate requested frequency/polarisation against product metadata at ingest; substitute only explicitly and visibly.

#### TOOL-02 - Publishing bucket enforces Public Access Prevention
- **Symptom.** `gs://iocl-nisar-slc/` "Public access prevention: enforced" (T:L962); a `storage.googleapis.com` link returns 403.
- **Root cause.** Bucket policy.
- **Fix.** Uploaded to `gs://iocl-nisar-slc/nepal_glof/trackR_overlay/` with explicit Content-Types (4 objects, 137.4 MiB); told the user it is download-and-open (`gsutil cp -r`, then `python -m http.server`) (T:L1133). Policy untouched.
- **Cost.** No live URL. **Evidence.** as cited; `V:errors_W5`.
- **Automation lesson.** Pre-flight the publish target's IAM/PAP and report the delivery mode (public, signed URL, download-only) as part of the output.

#### TOOL-03 - GDAL geolocation warp with auto bounds clipped the swath
- **Symptom.** Geocoded reference amplitude covered lon [83.9260, 86.0976] against a footprint [83.3723, 86.6065], valid coverage 89.0% (T:L1018); controlled test: auto bounds 542x408, 92.0% valid; explicit bounds 802x672, 65.3% valid (T:L1051).
- **Root cause.** Without `outputBounds`, GDAL's suggested output extent samples the geolocation arrays coarsely and misses a rotated parallelogram's corners (reproduced on a synthetic grid in GDAL 3.12.4, `V:errors_W5`).
- **Fix.** Explicit bounds from lon/lat min/max (`R/tools/slc_amp_overlay.py:186-221`, docstring records the measurement).
- **Cost.** Detected 01:19:26, test 01:26:40, rebuild with `--reuse` 01:27:23 -> 01:35:40 (T:L1021, T:L1051, T:L1064, T:L1071): ~16 min. (The "about 5 min" in errors_W1 and "about 20 min" in errors_W5 are both superseded by these timestamps.)
- **Evidence.** as cited.
- **Automation lesson.** Always pass explicit bounds; gate valid fraction against footprint/bbox (G14); a too-high valid fraction is a failure signal.

#### TOOL-04 - GDAL-Python dangling dataset
- **Symptom.** `TypeError: in method 'Band_DataType_get', argument 1 of type 'GDALRasterBandShadow *'` in two overlay diagnostics on Sep 4 (T:L1024; T:L1163 "GDAL dataset garbage-collected") and in `compare_four_way.py` attempt 1, 1 s after start (`C/logs/compare_four_way_attempt1.log:17-27`, 13:09:01 -> 13:09:02, EXIT=1).
- **Root cause.** Chained `gdal.Open(p).GetRasterBand(1).ReadAsArray()`; the Dataset is collected while its Band is used. The first draft of the comparison tool reused the pattern 10 days after it had been diagnosed.
- **Fix.** Dataset held in a local with a comment (`R/tools/compare_four_way.py:121-132`).
- **Cost.** ~2 min per occurrence (three).
- **Evidence.** as cited; `V:errors_W5`.
- **Automation lesson.** One shared raster I/O module; lint for `gdal.Open(...).GetRasterBand(`; fix traps in the shared helper, not in the script that hit them.

#### TOOL-05 - Sinc-ringing fill broke the dB stretch; `_work` deleted by default
- **Symptom.** "pooled dB stretch: -133.97 .. 4.74" (T:L1071). Inspecting intermediates failed because `_work` was gone ("I didn't pass --keep-tif", T:L1083).
- **Root cause.** fine_resample's sinc kernel rings across the data/fill boundary, leaving finite ~1e-7 values (-134 dB) that pass `isfinite` and drag the 2nd percentile down; the tool removes `_work` unless `--keep-tif` (`slc_amp_overlay.py:608-609`).
- **Fix.** Fill floor = scene median - 40 dB, sub-floor pixels transparent (1.49-1.59% per layer); stretch -18.67 .. +4.80 dB (T:L1103); `--reuse` and `--html-only` added (`slc_amp_overlay.py:421-435`).
- **Cost.** Rebuild 01:37:38 -> 01:46:10 = 8 m 32 s (T:L1094, T:L1103). The assistant had estimated ~40 min; errors_W1's "About 40 min" is that estimate, not the measurement.
- **Evidence.** as cited.
- **Automation lesson.** Validity masks from explicit fill masks or relative floors; keep expensive intermediates by default, content-addressed; cleanup is an explicit step after QA passes.

#### TOOL-06 - USER CAUGHT: layer switcher hidden; "verification" by grep
- **Symptom.** The assistant: "Done. What you asked for was already what shipped - I verified it rather than taking my own word for it." (T:L1188). The user: "omg I said we wanted 3 layers ... what in the world did you alter in the present html bro, this a absolute joke" (T:L1191).
- **Root cause.** The metadata panel (`position:absolute; top:10px; right:10px; z-index:1000`) covered Leaflet's `L.control.layers` corner (T:L1199). The "verification" grepped the HTML for layer names and checked the PNG stretch; its own empirical check had a garbage-collected GDAL dataset and compared a 4326 raster with a 3857 PNG (T:L1163). The rendered page was never looked at.
- **Fix.** Switcher rebuilt inside the panel (radio rows, 1/2/3 keys, space to blink), Leaflet's control removed, only `index.html` regenerated from `manifest.json` (`slc_amp_overlay.py:297-302`).
- **Cost.** ~5-10 min rework, user frustration, and the credibility cost of a false "verified".
- **Evidence.** as cited; `V:errors_W1_rslc_full.json` entry 11; `V:errors_W5`.
- **Automation lesson.** HTML deliverables are verified by rendering and interacting (G18). "Verified" is reserved for checks on the artifact the user sees.

#### TOOL-07 - GDAL geolocation arrays assume corner coordinates
- **Symptom.** Risk of a silent half-pixel shift between geocoded RSLC and GSLC layers.
- **Root cause.** GDAL's GEOLOCATION default is `TOP_LEFT_CORNER`; rdr2geo lon/lat are pixel centres. Audit synthetic test (GDAL 3.12.4): chosen-minus-true index averages -0.4 px without the key (60% exact), +0.1 px with `PIXEL_CENTER` (90% exact) (`V:errors_W5`).
- **Fix.** `GEOREFERENCING_CONVENTION=PIXEL_CENTER` (`compare_four_way.py:501`) plus per-pixel back-projection (`:520-550`); v1 lookup mean dx -0.26 m, dy -0.07 m (`C/logs/compare_four_way.log:11`).
- **Cost.** None; set before first use in the tool.
- **Evidence.** as cited.
- **Automation lesson.** The geocoding module sets the convention explicitly and always runs the back-projection gate (G15).

#### TOOL-08 - PIXEL_CENTER fix not ported to `slc_amp_overlay.py`
- **Symptom.** `slc_amp_overlay.py` `geocode()` sets `X_DATASET`, `Y_DATASET`, offsets, steps and SRS but no `GEOREFERENCING_CONVENTION` (`:209-217`, measured here), while its lon/lat are look-box means.
- **Root cause.** The trap was fixed in the comparison tool on Sep 14 but not back-ported to the Sep 4 tool using the same mechanism.
- **Fix.** None.
- **Cost.** The delivered overlay is registered about half a multilooked cell off the basemap (about 20 m in azimuth at 9 looks, per `V:errors_W5`; not measured on the product), consistently across its three layers.
- **Evidence.** as cited.
- **Automation lesson.** One shared geocode-by-geolocation-arrays function; a trap fix is applied repository-wide by grep.

#### TOOL-09 - `SRS: EPSG:4326` in GEOLOCATION metadata
- **Symptom.** `ERROR 1: missing [` on the ad-hoc geoloc warp (T:L4405 metadata, T:L4413).
- **Root cause.** GDAL parses that item as WKT. Audit test: identical valid fraction with the EPSG string or WKT (rc=0 both); GDAL falls back to WGS84, so it would be silently wrong for non-WGS84 arrays (`V:errors_W5`).
- **Fix.** Full WKT (`compare_four_way.py:502`; `slc_amp_overlay.py:215-216`).
- **Cost.** Negligible. **Evidence.** as cited.
- **Automation lesson.** Write SRS items with `osr.ExportToWkt()`; treat GDAL `ERROR` lines as gate events (G16).

#### TOOL-10 - Empty warp misdiagnosed as a relative-path trap
- **Symptom.** After `gdalwarp -geoloc` of the cropped RIFG: "Too many points (529 out of 529) failed to transform" and "warped output nonzero: 0.00%" (T:L4394, T:L4400). The assistant concluded GDAL resolves `X_DATASET`/`Y_DATASET` relative to the VRT's directory and that relative paths "silently produce an EMPTY output", and wrote that into STATE.md and the v1 tool docstring.
- **Root cause.** The check read window (0,0,2000,2000) (T:L4399), which is 0% valid even in the final correct lookup because it lies outside the v1 crop footprint (NW coverage gap, R_crop document). Audit test in GDAL 3.12.4: CWD-relative paths warp correctly (65.8% nonzero, same as absolute); VRT-relative paths fail loudly (`ERROR 4`, rc=1). The same "Too many points" warning appears in the successful absolute-path warp (`C/logs/compare_four_way_attempt2.log:12`).
- **Fix.** Claim withdrawn (`R/STATE.md:97-98`; `R/tools/compare_four_way.py:61-63`). Absolute paths kept as hygiene. (errors_W3 entry 25 records this as a fixed R_crop trap; that attribution and diagnosis are superseded, `V:critic.json`.)
- **Cost.** ~4 min and a restarted warp; a false trap entered the knowledge base and hid the real coverage problem for hours.
- **Evidence.** as cited.
- **Automation lesson.** Validate warps on a whole-raster decimated valid fraction against the expected footprint; never record a root cause without a controlled A/B test.

#### TOOL-11 - Shadow/layover backmap fill
- **Symptom.** First lookup check: mean dx/dy 0.07/0.02 m but p95 51.84 m, max 4571.5 m (`compare_four_way_attempt2.log:15`); per-pixel check p95 50.46 m, max 8954.13 m (`compare_four_way.log:11`).
- **Root cause.** Shadowed or laid-over ground has no unique radar sample; GDAL's backmap interpolates indices into those holes. Audit: 96% of excluded pixels lie >500 m from the crop-footprint edge, around ~19k interior holes (2.4% of the lattice) (`V:errors_W5`).
- **Fix.** Back-project every lookup pixel, keep within 15 m (`--geoloc-tol`, `compare_four_way.py:365`); excluded 8.8% of valid pixels (7.14% of the AOI); kept p95 5.70 m; a no-mask sensitivity result is written.
- **Cost.** One extra comparison run; 7.1% of the AOI excluded from cross-track statistics.
- **Evidence.** as cited.
- **Automation lesson.** Validate radar-to-map lookups per pixel; use the rdr2geo layover/shadow layer as a mask; report every mask's sensitivity beside the statistic.

#### TOOL-12 - 1549 s lookup warp; stall suspected
- **Symptom.** "lookup warped in 1549s" (`compare_four_way_attempt2.log:14`). At 149 s the process was at 54.7% CPU, 1.57 GB RSS, no output file (T:L4584); the assistant suspected a stall and misread the clock (T:L4587).
- **Root cause.** GDAL builds the geolocation backmap from the 11219x14719 float64 lon/lat arrays in a single-threaded phase before writing output; v1 used `errorThreshold=0` and nearest neighbour over a 6498x10350 lattice (`legacy/compare_four_way_v1.py:461-464`). The contribution of `errorThreshold=0` was never measured.
- **Fix.** Lookup cached; the rerun reached the lookup check in 102 s (`compare_four_way.log:11`).
- **Cost.** ~26 min once.
- **Evidence.** as cited.
- **Automation lesson.** Build lookups once per (radar grid, DEM, lattice, convention, threshold) with that identity in the cache key; log the backmap phase explicitly so heartbeat gates (G8) do not misfire; benchmark `errorThreshold` before choosing.

#### CMP-01 - Loop-variable shadowing crash; results held in memory
- **Symptom.** `TypeError: unsupported operand type(s) for /: 'str' and 'int'` at `compare_four_way.py` line 629 after 1843.6 s (`compare_four_way_attempt2.log:50-54`, 13:11:15 -> 13:42:00, EXIT=1).
- **Root cause.** `b = args.buffer` rebound by `for a, b in pairs:`; all statistics were in memory and `comparison.json` was written only at the very end.
- **Fix.** Loop renamed `p, q`; I3 uses `args.buffer` directly (`legacy/compare_four_way_v1.py:632`, `:678`). With cached layers the rerun took 4 m 08 s. v2 still writes `comparison.json` once at the end (`compare_four_way.py:936`).
- **Cost.** 30.7 min run lost, mitigated to ~4 min by caching.
- **Evidence.** as cited; T:L4601.
- **Automation lesson.** Stage functions without shared mutable locals; per-stage result files written as each stage finishes; linting before long runs.

#### CMP-02 - Wrong frame-shift prediction; metres labelled as pixels
- **Symptom.** The assistant predicted cropped dense offsets equal full offsets minus the frame shift (+824 lines, -13 samples); measured raw difference median 0.0004 vs 824.0004 for the shifted hypothesis (T:L4587; `compare_four_way.log` C1). The C1 numbers were labelled `median_px`/`std_px`.
- **Root cause.** RUNW pixelOffsets are frame-independent residuals against geometry, and their units attribute is `meters` (`V:critic.json` contradiction 4), so the hypothesis test subtracted lines from metres.
- **Fix.** v2 reports metres and converts with RSLC spacings (`compare_four_way.py:30-32`); unrun.
- **Cost.** Negligible compute; mislabelled numbers quoted in logs and verification.
- **Evidence.** as cited.
- **Automation lesson.** Read units from dataset attributes into the data model; test assumptions against data before building checks on them.

#### CMP-03 - Inverted a/b labels and opposite sign conventions
- **Symptom.** `R_full_vs_R_crop_radar_9x8` and `G_full_vs_G_crop_40m` had `median_a` = crop; the `cross_track_5m_lattice` block used a = full, so the same named comparison has opposite signs in one JSON (verifier V4; `V:critic.json` contradiction 5).
- **Root cause.** Positional `screen_agreement(io_c, io_f_win, im)` and `(gio_c, gio_f, m40)` under full-first names (`legacy/compare_four_way_v1.py:681`, `:695`).
- **Fix.** v2 declares one convention, "P__vs__Q ALWAYS means P minus Q" (`compare_four_way.py:12-20`); unrun.
- **Cost.** Mislabelled values reached the verification prompt.
- **Evidence.** as cited; `V:verdicts.json` V4.
- **Automation lesson.** Keyword arguments and self-describing fields (`offset_crop_minus_full`); a unit test that swaps inputs and checks the sign.

#### CMP-04 - `nearest_cycle_combo` attribution meaningless
- **Symptom.** The +48.97 rad R_full-vs-R_crop offset was attributed to `{'m_A': -7, 'n_B': -8, 'residual_rad': 1.525}`; verifier V1 decomposed it differently (science: R_crop document).
- **Root cause.** A 17x17 integer grid with 76.17 and -72.96 rad steps (sum 3.21 rad) fits almost any constant (`legacy/compare_four_way_v1.py:272-281`).
- **Fix.** Removed in v2; unwrapped A and B phases differenced directly (`compare_four_way.py:41-44`); unrun.
- **Cost.** Supported a wrong narrative.
- **Evidence.** as cited; `V:verdicts.json` V1.
- **Automation lesson.** Attribute offsets by per-band, per-component differencing, not by fitting integer combinations to a scalar.

#### CMP-05 - NaN nodata with `!= 0` masks
- **Symptom.** `C/comparison/report/figures.json` `charts.phase_diff_hist.G_full__G_crop_5m` has 90 of 90 bins NaN; `RuntimeWarning: invalid value encountered in divide` in `C/logs/report_figures.log` (`V:errors_W5`).
- **Root cause.** G_crop interferograms use NaN nodata while other layers use 0; `common &= ifg[k] != 0` admits NaN (`legacy/compare_four_way_v1.py:617`; `R/tools/report_figures.py:155-160`, still present).
- **Fix.** v2 comparison sanitises reads with `nan_to_num` helpers (`compare_four_way.py:166-171`; not traced for every read); `report_figures.py` unchanged.
- **Cost.** One empty chart; common-mask fraction slightly overstated (34,810 NaN pixels, 0.07%).
- **Evidence.** as cited.
- **Automation lesson.** One nodata convention recorded in metadata; masks built by one helper with `np.isfinite(x) & (x != 0)`; assert charts contain no NaN before publishing.

#### CMP-06 - Existence-keyed caches; side-effect writes into a workflow product directory
- **Symptom.** v1 `cached(name, build)` returned any existing layer regardless of inputs (`legacy/compare_four_way_v1.py:541-547`); `lut_rowcol.tif`'s name encoded neither convention nor threshold; the tool wrote `aoi/pairs/.../trackR/coherence_A_HH_win3.tif` if absent (`:567-573`).
- **Root cause.** Caching added for reboot resilience used filename existence as validity, breaking the output-identity rule.
- **Fix.** v2 keys caches by a SHA-1 of input paths, sizes, mtimes and parameters (`compare_four_way.py:160-163`, `:474-478`, `:638-646`) and writes only under `<case>/<out>/` (`:372-373`); unrun.
- **Cost.** Latent: a rebuilt lookup combined with layers from the old one.
- **Evidence.** as cited.
- **Automation lesson.** Content- or manifest-keyed caches with dependent invalidation; comparison tools never write into workflow product directories.

#### CMP-07 - Hard-coded case constants
- **Symptom.** Tool works only for this case.
- **Root cause.** v1: `--case`, `--kml`, `--tag` defaults, `EPSG:32645`, `ly, lx = 9, 8`, asymmetric legacy product names (`legacy/compare_four_way_v1.py:296`, `:461`, `:674`). v2 still: `--case` (`:357`), `--kml` (`:360`), `EPSG:32645` (`:506`), and the asymmetric names `amp_A_HH_{d}.tif` vs `amp_A_HH_1x1_{d}.tif` (`:690-691`).
- **Fix.** Partial.
- **Cost.** Porting needs code edits.
- **Evidence.** as cited.
- **Automation lesson.** Drive comparisons from the legs' run manifests (paths, looks, EPSG, centre frequencies from HDF5); alias legacy products to one naming template.

#### CMP-08 - Nearest-neighbour geocoding caps 5 m cross-geometry agreement
- **Symptom.** R_full vs G_full wrapped-phase agreement 0.619 at 5 m vs 0.887 at 40 m (`V:verdicts.json` V2).
- **Root cause.** The lookup picks the nearest radar sample (up to half a pixel away) while GSLC interpolates to the cell centre; V2 stratified agreement by lookup distance (0.767 under 1 m, 0.655 at 1-2 m, 0.583 at 2-3 m).
- **Fix.** Documented; 40 m is the fair cross-track number (`compare_four_way.py:68-71`).
- **Cost.** The 5 m number is a bound, not a measure of agreement.
- **Evidence.** as cited.
- **Automation lesson.** For native-resolution cross-geometry comparisons, geocode with an interpolating ISCE3 geocoder or stratify by lookup distance, and say which.

#### CMP-09 - Cause-laden naming ("planar ramp")
- **Symptom.** `phase_agreement` fitted a plane and a code comment called it "a reference-phase / baseline-handling difference" (`legacy/compare_four_way_v1.py:203-205` per `V:critic.json`); figure `f04_ramp_R_full__G_full.webp` exists in `C/comparison/report/fig/`.
- **Root cause.** A physical attribution was written into code and figure names before it was tested; V2 refuted it (the physics belongs to the G_full/G_crop documents; `V:critic.json` also records that V2's Doppler constant used the wrong rate).
- **Fix.** v2 comparison reports quadrant gradients and tests a stated model with the 1520 Hz line rate (`compare_four_way.py:45-51`); `report_figures.py:204-207` still draws "the R-G ramp".
- **Cost.** Risk of misinforming the team report.
- **Evidence.** as cited.
- **Automation lesson.** Comparison modules report residual structure neutrally; physical attribution is a separate, parameter-free test with its own output.

### 11.3 Cross-references (entries whose home is another document)

| Item | Home | One-line pointer |
|---|---|---|
| gslcB `pgrep` waiter deadlock (~4 h idle, "Started - safe to close the laptop") | G_full | same bug class as OPS-01 |
| Wait loop matched a stale `ABORT` line in an appended log | G_full | motivates Section 3.2 rule 1 |
| Overwrite-safety check passed vacuously (missing scratchpad dir) | G_full | gate G17 |
| VM stop Sep 8 18:53 during GSLC finalisation; truncated `20260726_gslc_freqB.h5` declared complete | G_full | gates G11-G12 |
| Harness OAuth/session-limit effect on the GSLC ionosphere reporting | G_full | summarised in OPS-15 |
| 46 GB of benchmark scratch deleted during 1x1 snaphu; projection double-counted (byte counts to reconcile) | R_full | disk model Section 8.2 |
| OOM mechanism in `generate_insar_mask` (5.77 GB retained, ~8.7 GB transient) | R_full | OPS-09 covers the hardware side |
| Disk gate ignored products; scratch model 2.6x low | R_full | Section 8.2 |
| SIGKILL left the RIFG HDF5 structurally corrupt | R_full | gate G12, atomic writes |
| Resume hint recommends `--force`, which wipes scratch | R_full | Section 5.5 |
| `lines_per_block` tuned on freq B used 8x memory on freq A | R_full | Section 6 |
| 1x1 RUNW ionosphere layer is all zeros but was described as present | R_full | gate G13 |
| Benchmark produced on a patched nisar with no provenance record | R_full | OPS-18 |
| Output identity: RUNW/runconfig/log names missing unwrap looks | R_full | Section 3.2 rule 3 |
| Crop-feasibility finding lost to compaction; 68-agent sweep hit the session limit | R_crop | OPS-15 covers the limit |
| Crop origin not a multiple of the looks | R_crop | - |
| Cropped granules miss the NW of the AOI (native Doppler in `rslc_subset.py`) | R_crop | gate G19 |
| A failed ingest left a stale `stack.json`; grep filters hid ERROR lines | R_crop | never filter ERROR out of monitoring output |
| R_crop launch parameters lived only in `/tmp` CLI flags | R_crop | OPS-14 |
| Disk-gate model omits the ionosphere sub-run | R_crop | Section 8.2 |
| `stack.json` regenerated while the R_crop chain was running | G_crop | Section 5.5 |
| freqAB resolver bug deliberately deferred in gridgate | G_crop | Section 13 |
| Whole-scene vs AOI ionosphere median called "within the degeneracy" | G_crop | Section 13 |
| Untriaged GDAL `ERROR 5 ... Access window out of range` in every GSLC run | G_full (referenceTerrainHeight all-NaN) | gate G16 |
| Provenance sidecars per stage, overwritten | G_full | Section 3.1 |

---

