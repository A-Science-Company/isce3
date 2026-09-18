# Operations, tooling and orchestration lessons for automating NISAR ISCE3 workflows on a VM

Case: Nepal GLOF, NISAR pair 20260714 x 20260726, ISCE3 0.25.12 (`isce3_env`).
State of the machine and files as read on 2026-09-14 between 15:20 and 15:35 UTC, while the
`aoiv2` run (cropped legs v2) was live. Nothing in this document was produced by running
processing; every measurement below is a metadata read, a `du`/`df`/`lsblk` read, a code read,
or a quotation of an existing log, product or verification file.
Sections 1-10 were fact-checked against the machine on 2026-09-15 ~02:05 UTC; notes about state
that changed after the original reading are dated. Section 11 was not re-checked.

This is the cross-cutting companion to the four workflow documents (R_full, G_full, R_crop,
G_crop = `R/docs/WF1_RSLC_FULL_TILE.md`, `WF2_GSLC_FULL_TILE.md`, `WF3_RSLC_CROPPED.md`,
`WF4_GSLC_CROPPED.md`) and the comparison document (no such file on disk as of 2026-09-15). It covers everything that is not the science of one
workflow: the VM, the environment and its overlays, how long jobs were launched, supervised,
chained and verified, the tooling bugs that hit geocoding-for-QA and the comparison tools, and
what an orchestrator must do so that none of this depends on a person or an AI agent watching.

### Evidence conventions

| Tag | Meaning |
|---|---|
| `T:Lnnnn` | 0-based line index in the session transcript `/home/sharath/.claude/projects/-home-sharath-isce3/026455e4-62e7-4688-b0be-3c825e81d3cd.jsonl`. (`errors_W1_rslc_full.json` sometimes cites the same event one line higher.) |
| `V:<file>` | `/home/sharath/isce3/case_studies/nepal_glof/comparison/verification/<file>` (errors_W1..W5, verdicts.json, critic.json) |
| `C/` | `/home/sharath/isce3/case_studies/nepal_glof/` |
| `R/` | `/home/sharath/isce3/asc/nisar_workflows/` |
| `SP/` | `/home/sharath/miniforge3/envs/isce3_env/lib/python3.12/site-packages/` |
| "measured here" | read on 2026-09-14 15:20-15:35 UTC for this document |
| UNVERIFIED / OPEN | not established by evidence on disk; do not build on it |

---

## 1. Purpose, scope, and when to use this document

**Purpose.** Give the person automating the modular NISAR SLC-to-interferogram pipeline an
operational specification that is true of this VM, this environment and these tools: what the
host must look like before a run, how runs must be launched and supervised, what the logs and
state files are, which gates stop a bad run early, and which failures actually happened (with
cost) so the orchestrator is designed against them.

**In scope.**
- VM sizing, disk growth, swap, `/tmp`, reboots (Sections 2, 8).
- Conda environment, the four nisar overlays, `asc/env/verify.sh` (Sections 2, 7).
- Logs, provenance, run-script location, output-identity rules for operational artifacts (Section 3).
- tmux, `loginctl enable-linger`, agent-session-bound watchers, chaining, completion detection (Sections 4, 5).
- Thread counts, block sizes, tiling knobs that decide RAM and CPU (Section 6).
- Tooling bugs in GDAL-based geocoding for QA (`tools/slc_amp_overlay.py`) and the comparison
  tools (`tools/compare_four_way.py`, `tools/report_figures.py`) - the tooling lesson only, not
  the scientific result (Sections 7, 11).
- An orchestration contract (Section 12) and lessons on working with an AI coding agent (Section 13).

**Out of scope.** The science and the per-workflow ISCE3 behaviour of each leg (coregistration,
unwrapping, ionosphere, GSLC geocoding, cropping). Those live in the workflow documents and are
cross-referenced in Section 11.3.

**Use this document when** you are writing the launcher, supervisor, resource gates, state/resume
logic, or QA tooling of the pipeline, or when you operate a long ISCE3 run by hand on this VM.

**Do not use it** as the source for scientific parameter choices (looks, ionosphere filters,
snaphu cost settings beyond their memory impact) or for comparison results. It also does not
describe the aoi_v2 run's outcome: that run was still in progress at the time of writing
(`C/logs/aoi_v2_trackR.log` last line 15:30:31 `Topo progress (block 25/25)`;
`C/logs/aoi_v2_trackG.log` last line 15:32:30 in `gslc/20260726`). It has since finished:
`=== 2026-09-14T16:38:02Z AOI V2 DONE R=0 G=0 ===` (`C/logs/aoi_v2.log:239`).

---

## 2. Environment and inputs

### 2.1 VM history

| Period | RAM | Cores | Swap | Disk (fs size / free) | Evidence |
|---|---|---|---|---|---|
| Sep 3 - Sep 6 05:21 (boots -5, -4) | 3919 MiB | 2 | 8 GiB `/swapfile`, active | `/dev/sda1` 492G, 379G free on Sep 3 | T:L35 (`free -g`, `nproc` = 2), T:L1579 (`Mem: 3919`), T:L1650 (`/proc/swaps` lists `/swapfile 8388604`) |
| Sep 6 16:48 (boot -3) onwards | 31 GiB (MemTotal 33.66 GB) | 8 | **none active** | disk 500 -> 600 GB by user; partition/fs grown by assistant: 492G/218G free -> 591G/312G free | T:L1663 (user), T:L1682 ("31 GB RAM (was 3.9), 8 cores (was 2)"), T:L1686-L1687 (growpart/resize2fs), T:L2427 ("SwapTotal 0, overcommit_memory=1") |
| Sep 7 09:12 | same | same | none | 700 GB, grown by the user | T:L2611 |
| Sep 14 03:16 | same | same | none | 1000 GB; sda1 grown by assistant; free 92 -> 375 GB | T:L3456 (user: "but we need to make the hdd available"), T:L3461-L3462 |
| Measured here, 15:21 | 31 GiB total, 24 GiB available | 8 (AMD EPYC 7B12), GCE `e2-standard-8` | `/proc/swaps` empty; `/swapfile` (8 GiB, Sep 3 16:48) exists but is not in `/etc/fstab` | `lsblk`: sda 1000G, sda1 999.9G; `df /`: 985G size, 630G used, 315G avail | `free -g`, `nproc`, GCE metadata `machine-type`, `cat /proc/swaps`, `/etc/fstab`, `lsblk`, `df -h` |

Other host facts measured here: `/tmp` is **tmpfs, size 16 GiB (RAM-backed)** (`findmnt /tmp`);
`vm.overcommit_memory = 1` (allocations never fail, the OOM killer SIGKILLs instead - no Python
traceback); `loginctl show-user sharath` -> `Linger=yes`; `KillUserProcesses` is the compiled
default `false` (T:L1389-L1390, T:L1438); tmux 3.5a, socket `/tmp/tmux-1000/default` (tmpfs, so
gone after reboot; the server also exits when its last session ends, so "no server running on
/tmp/tmux-1000/default" at T:L4933, 14:59 Sep 14, was not a reboot).

### 2.2 Boot history (`journalctl --list-boots`, measured here)

| Boot | First entry (UTC) | Last entry (UTC) | What it meant operationally |
|---|---|---|---|
| -6 | Aug 28 04:15 | Aug 28 08:52 | pre-project |
| -5 | Sep 3 06:15 | Sep 3 15:42 | environment bring-up |
| -4 | Sep 3 15:43 | Sep 6 05:21 | 3.9 GB box; freq-B 9x1 run, overlay, freq-A 1x1 OOM (Sep 5 16:14) |
| -3 | Sep 6 16:48 | **Sep 8 18:53:22** | after RAM/CPU resize. Ended during finalisation of `L2_GSLC/20260726_gslc_freqB.h5` by an ACPI power-off (`Sep 08 18:53:18 systemd-logind: Power key pressed short. Powering off...`, `journalctl -b`), i.e. an instance stop, not a crash; who issued it is not recorded (see G_full document) |
| -2 | Sep 11 06:17 | Sep 11 09:52 | no session activity in the transcript; ended by ACPI power-off (09:51:58); initiator UNVERIFIED |
| -1 | Sep 14 02:44 | Sep 14 10:45:50 | tmux server gone on resume (T:L3417); ended by ACPI power-off at 10:45:45, about 20 s after the assistant said powering off was safe (T:L4457) |
| 0 | Sep 14 12:52 | (current at time of writing; ended Sep 14 16:45:25) | started after the user's power-off of boot -1 (T:L4484 "up 1 minute" at 12:54); ended by ACPI power-off at 16:45:21 after the aoiv2 run finished |

Indices are as listed at 15:21 Sep 14. Every listed boot ended with `systemd-logind: Power key pressed short. Powering off...`
(an ACPI power-off, which is how a GCE instance stop reaches the guest); the initiator of each stop is
not visible in the guest. A further boot began Sep 15 01:19:53, so
`journalctl --list-boots` now lists every row above one index lower (Aug 28 = -7, Sep 14 12:52 = -1).

Note: `V:errors_W5_ops_and_comparison.json` says "only two boots are evidenced, both on Sep 14";
that is wrong (`V:critic.json` contradiction 8, and the table above).

### 2.3 Software environment

| Item | Value (measured here) | Evidence |
|---|---|---|
| Conda | `/home/sharath/miniforge3`; envs `base`, `isce3_env`, `s1-burst`. **`dolphin_env` absent** | `conda env list` |
| isce3 / GDAL / h5py / snaphu-py | 0.25.12 / 3.12.4 / 3.16.0 / 0.4.1 | `python -c "import isce3, osgeo, h5py, snaphu"` |
| Python | 3.12 (`SP/` path) | |
| Environment check | `asc/env/verify.sh` (not `tools/verify.sh`) | `git log -- asc/env/verify.sh` |
| Overlay tool | `R/tools/apply_patches.py`, sources in `R/tools/patches/` | file read |

`verify.sh` history (`git log --date=iso -- asc/env/verify.sh`):

| Commit | Date | Change |
|---|---|---|
| 24bfc029 | 2026-08-13 | initial "verified ISCE3 environment setup" (Co-Authored-By Claude) |
| 0b2b4a63 | 2026-09-03 18:46 +0530 | adds `apply_patches.py --check` section; first overlay (isce3#372) |
| 9f69cf06 | 2026-09-03 20:37 +0530 | three verify.sh bugs exposed by the Docker build (entry OPS-17) |

### 2.4 Overlay install state (measured here)

`cmp` of each patch source against the installed file, and `apply_patches.py --check` output:

| Patch (`PATCHES` in `R/tools/apply_patches.py:39-121`) | Installed target | `cmp` | `.orig` backup | `--check` |
|---|---|---|---|---|
| `patches/resample_slc_v2.py` (isce3#372, f42cea75) | `SP/nisar/workflows/resample_slc_v2.py` | identical | present | already applied |
| `patches/insar_utils.py` (vectorised `generate_insar_mask`) | `SP/nisar/products/insar/utils.py` | identical | present | already applied |
| `patches/h5_prep.py` (RUNW_STANDALONE/GUNW_STANDALONE) | `SP/nisar/workflows/h5_prep.py` | identical | present | already applied |
| `patches/unwrap.py` (snaphu inputs streamed from disk) | `SP/nisar/workflows/unwrap.py` | identical | present | already applied |

Traps in this state (details in Section 7.1 and OPS-18):
- Installed-file mtimes (Sep 3 15:29, Sep 6 19:07, Sep 7 03:50, Sep 7 04:20) equal the patch
  **source** mtimes because `shutil.copy2` preserves them (`apply_patches.py:196`). When a patch
  was applied is recorded nowhere. `.orig` mtimes are all 2026-05-08 00:49:26 (the conda package's).
- Only `resample_slc_v2.py` is committed. The other three patch sources,
  `patches/insar_mask_vectorized.py`, and the 60-line `PATCHES` extension of `apply_patches.py`
  are untracked or uncommitted (`git status`: `?? tools/patches/h5_prep.py`, `insar_utils.py`,
  `unwrap.py`, `insar_mask_vectorized.py`; ` M tools/apply_patches.py`).
- The benchmark products carry no record of which overlays were active (the provenance issue
  belongs to the R_full document).

### 2.5 Preconditions to CHECK before any long run

| Check | Command | Pass criterion | Measured here |
|---|---|---|---|
| Right env active | `conda activate isce3_env && python -c "import isce3; print(isce3.__version__)"` | `0.25.12` | 0.25.12 |
| Overlays present | `python R/tools/apply_patches.py --check` | every patch "already applied" (note: exit code is 0 even when a patch is NOT applied, see OPS-18) | 4/4 already applied |
| Usable disk | `lsblk /dev/sda; df -BG /` | partition size equals disk size, and fs size equals partition size; free >= stage bill + `min_free_gb` | 1000G / 999.9G / 985G; 315G free |
| RAM and swap | `free -g; cat /proc/swaps; cat /proc/sys/vm/overcommit_memory` | stage peak estimate (Section 8) < MemAvailable; know whether swap exists | 24G available, no swap, overcommit 1 |
| Supervisor survives logout | `loginctl show-user $USER -p Linger` | `Linger=yes` | yes |
| No competing heavy job | `tmux ls`; PID files of the orchestrator (not `pgrep -f`) | only intended sessions | `aoiv2` (created 15:03:44); load average 16.95 on 8 cores at 15:21 |
| Outputs not on tmpfs | `findmnt -T <path>` for every output, scratch and script path | FSTYPE is not `tmpfs` | `/tmp` is tmpfs |
| Boot id recorded | `cat /proc/sys/kernel/random/boot_id` | stored in the run manifest at stage start | `0abadedc-61d8-49c0-bb1b-47eb26e3b1dd` |

---

## 3. Outputs: logs, provenance, state and run scripts

### 3.1 Layout of operational artifacts

| Artifact | Path pattern | Written by | Format | Measured here |
|---|---|---|---|---|
| Driver run log | `<root>/logs/track_r_<YYYYMMDDTHHMMSSZ>.log`, `<root>/logs/track_g_<stamp>.log`; `<root>` = `out_root` or `case_dir` | `R/run_track_r.py:288-296`, `R/run_track_g.py:329-339`, `R/nisar_wf/config.py:941-960`, `R/nisar_wf/util.py:111-115` (append mode) | text, UTC-stamped lines with `[Step n/N | pct | elapsed]` | `C/logs/`: 44 driver logs; `C/aoi_v2/logs/`: 5 at 15:21 (10 after the run finished) |
| ISCE3 journal log | `logging.path` of the emitted runconfig, e.g. `C/logs/insar_20260714_20260726_A_HH_1x1.log`, `write_mode: a` | ISCE3 journal via `SP/nisar/workflows/runconfig.py:94-103` (enabled by default: `yaml_argparse.py:48-49`) | pyre journal text | 3.85 MB for the benchmark 1x1 run |
| Wrapper console log | `C/logs/<name>.log` (e.g. `aoi_v2.log`, `aoi_v2_trackR.log`, `aoi_v2_trackG.log`, `trackG_gslcB.log`, `compare_four_way.log`) | the wrapper's `>> $LOG 2>&1` | text + `=== <UTC> <step> EXIT=<rc> ===` markers | whole `C/logs/` = 31 MB |
| Wrapper script | `C/logs/run_*.sh` | hand-written | bash | 4 surviving: `run_aoi_v2.sh`, `run_aoi_aligned_trackr.sh`, `run_compare.sh`, `run_report_figures.sh` (plus `run_compare_v2.sh` and `run_report_figures_v2.sh`, Sep 15) |
| Emitted runconfigs | `<root>/cfg/insar_<ref>_<sec>_<F>_<pol>_<crossmul looks>.yaml`, `gslc_<date>_freq<F>.yaml` | drivers | YAML | `C/cfg/`: 8 files |
| Per-stage provenance | `<root>/provenance/{ingest,dem,gslc,gridgate,igram}.json` | Track G stages | JSON | `C/provenance/`: 28 K, 5 files (per stage, overwritten per run - G_full document) |
| Step timing | `<root>/time_summary.txt` | drivers (`config.py:983-984`) | TSV `timestamp step duration` | present in `C/` and `C/aoi_v2/` |
| Comparison state | `C/comparison/` (v1), `C/comparison_v2/` (v2 default `--out`, `compare_four_way.py:413`) | comparison tools | GeoTIFF + JSON manifests | `C/comparison/` 1.1 G |
| Verification | `C/comparison/verification/*.json` | adversarial verification workflow | JSON | 8 files, 640 KB (10 files by Sep 15 01:27) |
| Resume document | `R/STATE.md` | by hand | Markdown | mtime Sep 14 15:29 |

### 3.2 Naming (output-identity) rules for operational artifacts

The project rule (user memory `nisar-output-identity-rule`): anything that changes a product's
bytes must be in its filename, because a re-run that writes a different grid to the same path
destroys the old product while every `exists()` check still passes. It applies to operational
artifacts too:

1. **One log per run ID, never appended across runs.** Appending let a wait loop match a stale
   `ABORT` line from the previous run (G_full document, `C/logs/trackG_igram8x8.log`), and forced
   hand renames of `compare_four_way.log` to `_attempt1/_attempt2` (T:L4561, T:L4623).
   `run_compare.sh:7-9` appends (`>>`), `run_report_figures.sh:7` truncates (`>`): inconsistent.
2. **Driver log stamps have 1-second resolution** (`run_track_r.py:291`) and `Logger` appends
   (`util.py:113-114`). Two driver invocations of the same track started in the same second would
   interleave in one file. Not observed (the aoi_v2 run produced `track_r_20260914T150832Z.log` and
   `...150833Z.log` one second apart); latent.
3. **Runconfig and journal-log names still omit the unwrap looks**: `C/cfg/insar_20260714_20260726_A_HH_1x1.yaml`
   now holds the 9x8 pass (`phase_unwrap` 9/8, `ntiles [4,4]`) and the 1x1 pass's config was
   overwritten (R_full document). An orchestrator must name every emitted config and log with
   the full parameter identity or a content hash.
4. Superseded outputs are renamed, not deleted, and the reason is in the name:
   `C/aoi_aligned_ABORTED_nativeDoppler` (18 G) and `C/L1_RSLC_AOI_ALIGNED_ABORTED_nativeDoppler`
   (3.1 G) (T:L4935). Good practice; keep it.

### 3.3 Where run scripts, manifests and intermediates must live

- **Never `/tmp`.** On this VM `/tmp` is a 16 GiB tmpfs: files there consume RAM and vanish on
  reboot. The 12:52 reboot erased every wrapper script and intermediate the assistant had put in
  its scratchpad (OPS-14). The only record of those CLI flags is the transcript.
- Wrapper scripts: the case tree (`C/logs/run_*.sh`, current practice since 13:08 Sep 14,
  T:L4545) or, better, versioned in the repository with the run ID and config hash in the
  manifest.
- Comparison caches: under the case (`compare_four_way.py:417-420` writes `<case>/<out>/layers/`).
- Set the working directory of every GDAL process to a budgeted scratch directory: GDAL wrote
  `_70284_15fill_filtmask_work.tif` (0 B), `_70284_15fill_val_work.tif` (35 MB) and
  `_70284_15fill_y_work.tif` (1.4 MB) into `C/` itself, mtime Sep 14 10:45 (see OPS-14).

---

## 4. Execution model

### 4.1 Operational stage table

This is the operational lifecycle every processing stage goes through, with the settings used
here and what was measured.

| Stage | Tool / entry point | What it does | Key knobs and values used here | Why | Measured |
|---|---|---|---|---|---|
| O1 Pre-flight | `apply_patches.py --check`; `lsblk`/`df`; driver `--only runconfig` (disk gate) | env, overlays, usable disk, disk bill | `track_r.min_free_gb: 60.0` (`R/configs/nepal_glof.yaml:516`), `enforce_disk_gate: true` (`:518`), scratch model 104.4 B/px (`R/nisar_wf/trackr.py:50-86`) | refuse runs that cannot finish | gate refused "needs 323.7 GiB of scratch but only 316.8 GiB is free" (`C/logs/track_r_20260906T173856Z.log:35`) although 118-127 GiB of that scratch already existed (R_full document); now credits existing scratch (`trackr.py:562-577`) |
| O2 Launch | `tmux new-session -d -s <name> <abs path to script>` | detach from any login or agent session | `Linger=yes` | survive laptop disconnect / client restart | parentage verified before the laptop closed, Sep 7 02:23 (T:L2100-L2102); run confirmed alive after the disconnect at 10:28 (T:L2647) |
| O3 Supervise | tmux session + wrapper `step`/`check` markers | record stage start/end and exit code | `set -o pipefail`; `check ${PIPESTATUS[0]}` (`C/logs/run_aoi_v2.sh:4,12-13`) | exit code must come from the worker, not `tee` | `C/logs/aoi_v2.log`: `subset 20260714 EXIT=0` (line 55) ... `dem EXIT=0` (line 238) |
| O4 Chain | one wrapper per DAG; parallel legs as `( ... ) &` + `wait` | order dependent stages without a human | `run_aoi_v2.sh:15-56`: subset x2 -> ingest -> dem -> {R: runconfig, insar} ‖ {G: gslc A, gslc B, gridgate, igram x3, ionosphere} | no idle gaps between stages; one shared ingest so `stack.json` is never rewritten under a running chain (`run_aoi_v2.sh:2-3`) | parallel legs: load average 16.95 on 8 cores at 15:21 (measured); effect on runtime UNVERIFIED |
| O5 Watch | outside the agent: none exists yet. Inside the agent: `until ! tmux has-session -t <name>; do sleep N; done` as a background task (T:L3725, T:L4623) | detect end of stage | poll 15-30 s | `has-session` cannot self-match (unlike `pgrep -f`) | agent-side watchers died with the client on Sep 5 (T:L1465); idle/undetected gaps of ~3 h, ~4 h, 68 min (Section 11) |
| O6 Validate | product checks (dataset inventory, content fraction, valid fraction vs footprint) | decide done vs failed | see Section 9 | exit 0 is not proof of a valid product | truncated GSLC 26/193 datasets declared complete (G_full document); zero-filled ionosphere layer described as present (R_full document) |
| O7 Publish | `gsutil cp -r` | deliver | bucket `gs://iocl-nisar-slc/`, Public Access Prevention enforced | policy unchanged | 4 objects, 137.4 MiB, download-only (T:L1133, TOOL-02) |
| O8 Resume | driver `--only`/`--start-step`; on-disk markers; `STATE.md` | restart after crash, reboot or client restart | see Section 5.5 | reboots happen; agent memory is not state | Sep 14 02:44 reboot: nothing lost because nothing was running (G_full document for the Sep 8 stop) |

### 4.2 Process supervision: tmux and linger

Verified process chain of a long run (T:L2100): `insar python <- python <- bash <- tmux server <- systemd (PID 1)`.

What `loginctl enable-linger` does and does not do:
- **Does:** keep `user-1000.slice` and the user's systemd manager alive when the last login session
  ends, so a tmux server started by that user is not torn down at logout. With `Linger=no` the
  nohup'd run sat in `user-1000.slice/session-32.scope` (T:L1390) and its survival across logout
  was not guaranteed.
- **Does not:** keep any process alive across a reboot or VM stop. Every reboot here killed the
  tmux server (T:L3417, T:L4484). The assistant told the user at T:L4457 that linger means "it
  survives reboots"; that was false (OPS-13) and was not corrected to the user (not found by grep
  of the transcript after T:L4457).
- Standard logind behaviour is that a lingering user's systemd `--user` manager is started at
  boot, so `systemd --user` units that are enabled would start again. That is UNTESTED on this VM;
  tmux sessions are not restarted by it.

`nohup cmd &` from an agent shell reparents to PID 1 but stays in the invoking session's scope;
it is not a supervisor, records no exit status, and was the launch mode of the lost 10:38 warp
(T:L4405).

### 4.3 What lives in the agent session (and dies with it)

Claude Code background Bash tasks and `Monitor` watchers are children of the client session:
- They died on the Sep 5 client restart (T:L1465 "Session restarted overnight - my watchers died with it"),
  so the OOM at 16:14:26 went unreported until the user asked at 19:10 (T:L1541).
- `Monitor` watchers time out (T:L497 "[Monitor timed out - re-arm if needed.]") and are rate
  limited (T:L736 "output rate too high").
- The harness blocks `sleep N; check` polling in foreground calls (T:L399, T:L3164, T:L3234, T:L4576).
- The harness runs each command as `/bin/bash -c source <snapshot> && <command>` (visible in
  `pgrep -af` output at T:L4584), so every string in a command is in that shell's argv. This is
  the root of the `pkill -f`/`pgrep -f` self-matches (OPS-01).

Automation consequence: failure detection and stage chaining must be done by the job's own
supervisor (wrapper + status file, systemd unit with `OnFailure=`, or a DAG runner), never by an
agent's watcher.

### 4.4 Per-stage notes on non-obvious behaviour

- **Two logs per ISCE3 run.** The driver log captures the child's stdout/stderr through
  `run_cmd` (`R/nisar_wf/util.py:256-293`). ISCE3's journal writes to the runconfig's
  `logging.path` (`SP/nisar/workflows/runconfig.py:94-103`), e.g. `C/cfg/insar_20260714_20260726_A_HH_1x1.yaml:92-94`.
  Progress of `nisar.workflows.insar` stages is in the journal log, not the console (T:L1499).
- **Child output is block-buffered even when the driver is started with `python -u`.**
  `run_cmd` passes `bufsize=1` (`util.py:280`), which only affects the parent's reading;
  the children are launched as `python -m nisar.workflows.insar` (`R/nisar_wf/trackr.py:691-695`)
  and `sys.executable -m nisar.workflows.gslc` (`R/nisar_wf/gslc.py:455-459`) with the inherited
  environment (`env=None`), and `-u` does not propagate to child interpreters. Only
  `PYTHONUNBUFFERED=1` in the environment would. Evidence of the effect: bursts of identical
  timestamps (18:48:58.61x) at the tail of `C/logs/track_g_20260908T165528Z.log` (G_full document).
- **Track R launches `python` from `PATH`** (`trackr.py:692`) while Track G uses `sys.executable`
  (`gslc.py:456`). A driver started with an absolute interpreter path in a shell whose `PATH`
  points to another env would run ISCE3 from the wrong env. Not observed; latent.
- **A failed top-level step exits the wrapper before the final marker.** In `run_aoi_v2.sh:13`
  `check` calls `exit` on the first non-zero code, so `AOI V2 DONE R=.. G=..` (`:56`) is never
  written. A watcher waiting only for `DONE` would wait forever; a watcher must combine
  `tmux has-session` (liveness) with the last `EXIT=` marker (outcome).
- **GDAL geolocation backmap is a silent, single-threaded phase.** The v1 lookup warp spent its
  first minutes with no output file at 54.7% CPU and 1.57 GB RSS (T:L4584) and took 1549 s in total
  (`C/logs/compare_four_way_attempt2.log:14`). A watcher keyed on output growth would call it stalled.

---

## 5. How to run long jobs

### 5.1 Wrapper template

This is the pattern of the wrappers that worked here (`C/logs/run_aoi_v2.sh`, `run_compare.sh`),
with the gaps found in this review closed (unbuffered children, per-run log directory, PID files,
explicit status file). It is a template, not a file on disk.

```bash
#!/usr/bin/env bash
# Keep -e OFF: every exit code is captured and written explicitly.
set -uo pipefail
export PYTHONUNBUFFERED=1                      # propagates to ISCE3 child interpreters; -u does not
source /home/sharath/miniforge3/etc/profile.d/conda.sh
conda activate isce3_env

C=/home/sharath/isce3/case_studies/nepal_glof
T=/home/sharath/isce3/asc/nisar_workflows
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_aoi_v2"      # one directory per run, never reused
RD="$C/runs/$RUN_ID"; mkdir -p "$RD"
cp "$0" "$RD/"                                 # the exact script travels with the run
cat /proc/sys/kernel/random/boot_id > "$RD/boot_id"

mark() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$RD/status"; }

stage() {                                      # stage <name> <command...>
  local name=$1; shift
  mark "STAGE_START $name"
  "$@" > "$RD/$name.log" 2>&1 &
  local pid=$!; echo "$pid" > "$RD/$name.pid"
  wait "$pid"; local rc=$?
  mark "STAGE_END $name EXIT=$rc"
  return "$rc"
}

cd "$T"
CFG=configs/nepal_glof_aoi_v2.yaml
stage ingest python run_track_r.py --config "$CFG" --only ingest || { mark "RUN_END EXIT=$?"; exit 1; }
stage dem    python run_track_r.py --config "$CFG" --only dem    || { mark "RUN_END EXIT=$?"; exit 1; }
# ... further stages, parallel legs as ( stage ... ) & with wait "$pid" per leg ...
mark "RUN_END EXIT=0"
```

If a pipe is unavoidable (`cmd 2>&1 | tee file`), take the worker's code with
`rc=${PIPESTATUS[0]}` immediately after the pipeline; never `echo EXIT=$?` (OPS-02). Note that in
the existing wrappers `python ... >> $LOG 2>&1; check ${PIPESTATUS[0]}` has no pipe, so
`PIPESTATUS[0]` equals `$?`: correct, but it hides the intent.

### 5.2 Launch, attach, inspect

```bash
tmux new-session -d -s aoiv2 /home/sharath/isce3/case_studies/nepal_glof/logs/run_aoi_v2.sh   # as used, T:L5008
tmux attach -t aoiv2              # Ctrl-b d to detach
tmux has-session -t aoiv2 && echo running || echo ended
grep -E '^=== .* EXIT=' /home/sharath/isce3/case_studies/nepal_glof/logs/aoi_v2*.log | tail
tail -f /home/sharath/isce3/case_studies/nepal_glof/aoi_v2/logs/insar_20260714_20260726_A_HH_1x1.log   # ISCE3 journal
```

Put the command in a script file and pass the script path to tmux. Inline tmux command strings
put the whole command (including any `pgrep` pattern) into the shell's argv; that is how the gslcB
waiter deadlocked (G_full document).

### 5.3 Driver commands (checked against argparse and `--help`)

`run_track_r.py` options (`R/run_track_r.py:176-211`): `--config/-c`, `--only STEP...`,
`--start-step`, `--stop-step`, `--force`, `--dry-run`, `--list-steps`, `--log-file`, `--quiet`,
`--frequency`, `--polarization`, `--looks AZ RG`, `--product-type`, `--pair REF SEC` (repeatable),
`--no-disk-gate`. Steps: `ingest`, `dem`, `runconfig`, `insar`, `qa`.

`run_track_g.py` options (`--help`): `--config/-c`, `--only`, `--start-step`, `--stop-step`,
`--force`, `--dry-run`, `--list-steps`, `--log-file`, `--quiet`, `--frequencies F...`,
`--polarizations P...`, `--dates YYYYMMDD...`, `--igram-freq F`, `--looks LY LX`, `--dem-source`.
Steps: `ingest`, `dem`, `gslc`, `gridgate`, `qa`, `igram`, `watermask`, `unwrap`, `overlay`.

Commands actually used by the live v2 wrapper (`C/logs/run_aoi_v2.sh:22-50`):

```bash
python -u run_track_r.py --config configs/nepal_glof_aoi_v2.yaml --only ingest
python -u run_track_r.py --config configs/nepal_glof_aoi_v2.yaml --only dem
python -u run_track_r.py --config configs/nepal_glof_aoi_v2.yaml --only runconfig
python -u run_track_r.py --config configs/nepal_glof_aoi_v2.yaml --only insar
python -u run_track_g.py --config configs/nepal_glof_aoi_v2.yaml --only gslc --frequencies A   # then B (loop)
python -u run_track_g.py --config configs/nepal_glof_aoi_v2.yaml --only gridgate --frequencies A B
python -u run_track_g.py --config configs/nepal_glof_aoi_v2.yaml --only igram --frequencies A B \
    --igram-freq A --looks 8 8 --dates 20260714 20260726   # loop: A 1 1, A 8 8, B 8 8
python -u tools/gslc_ionosphere.py --pair-dir <case>/aoi_v2/pairs/20260714_20260726/trackG \
    --freq-a-prefix ifg_A_HH_8x8 --freq-b-prefix ifg_B_HH_8x8 --nlooks 64 --coherence-threshold 0.5 \
    --median-filter-size 15 --sigma-km 10 --ntiles 4 4 --nproc 4 --cycle-search 3
```

Safe inspection commands: `python run_track_r.py --list-steps`, `python run_track_g.py --list-steps`,
`--help`. **`--dry-run` is not side-effect free** (OPS-19): it creates `<root>/logs/` and appends a
driver log.

### 5.4 Finding and stopping processes

| Do | Do not |
|---|---|
| Record the worker PID at launch (`$!` into `<stage>.pid`) and act on it: `kill -0 $(cat x.pid)`, `kill $(cat x.pid)` | `pkill -f "<pattern>"` or `pgrep -f "<pattern>"` in a command whose own text contains the pattern (OPS-01) |
| Check liveness with `tmux has-session -t NAME` or `systemctl --user is-active UNIT` | `while pgrep -f '<same text as the launch>'; do sleep 30; done` (the gslcB 4 h deadlock) |
| If a pattern search is unavoidable, bracket one character so the regex does not match its own literal: `pgrep -af "[n]epal_glof_aoi_aligned"` (used after the 14:59 recurrence, T:L4928). This only works if the unbracketed string appears nowhere else in the same command line | Chain real work after a kill in the same shell (`pkill ...; edit; tmux new-session ...`): if the kill takes out the shell, the rest silently never runs (T:L2462, T:L4923) |
| After any kill or launch, check the post-condition in a separate call (session gone / PID alive / file edited) | Filter `ps | grep -v grep` and trust it: it works only because the invoking shell line happens to contain the word `grep` |
| Stop a tmux-run stage with `tmux kill-session -t NAME`, then confirm with the PID file | Assume the stage stopped because the session is gone (children may be in their own process group - UNVERIFIED for snaphu workers) |

### 5.5 Resume and restart semantics, and their traps

| Situation | What the code does | Trap | Safe action |
|---|---|---|---|
| `insar` output exists | skip the pair (`R/nisar_wf/trackr.py:668-669`) | existence, not validity: a SIGKILL-corrupted RIFG would be skipped (R_full document) | validate the product (Section 9) and delete it by hand if invalid |
| `--force` on `insar` | `shutil.rmtree` of the pair's whole scratch tree (`trackr.py:686-688`) | destroys hours of rdr2geo/geo2rdr (127 GB at the time) | never use `--force` to resume; invalidate one stage |
| Driver's failure hint | prints `... --start-step insar` (e.g. `C/logs/track_r_20260904T124949Z.log:1107-1109`); epilog recommends `--start-step insar --force` (`R/run_track_r.py:172-173`) | following the epilog wipes scratch | ignore the epilog resume line |
| ISCE3 `Persistence` | resume state read from the journal log | cannot resume a crashed insar run in this setup (R_full document) | treat each ISCE3 invocation as all-or-nothing; resume at driver-stage granularity |
| Track G `--start-step` | earlier outputs "assumed present on disk" (`--help`) | a missing freq B pin or truncated GSLC is not detected (G_full, G_crop documents) | run `gridgate` and inventory checks before downstream stages |
| Reboot / VM stop | tmux gone, `/tmp` emptied, agent watchers gone | last log lines may be missing (block buffering); partially written HDF5 | compare boot id; re-validate every product written during the dead boot; restart the stage |
| Agent client restart | synthetic "Continue from where you left off." / "No response requested." turns (T:L3024-L3025, T:L4511-L4513) | nothing resumes by itself | orchestrator reports status from the run manifest |
| Shared `stack.json` | regenerated by `ingest` | regenerating it while another chain runs (G_crop document) | one ingest per run, before parallel legs (`run_aoi_v2.sh:2-3`) |

---

## 6. Parameter reference: resources, threads, blocks, tiles

| Parameter | Value used here | Config path:line | What it controls | Alternatives / notes |
|---|---|---|---|---|
| `track_r.block_budget_mb` | 256.0 | `R/configs/nepal_glof.yaml:527`; v2 `nepal_glof_aoi_v2.yaml:564` | memory per streaming block; lines are derived as `min(cap, budget // (width x bytes_per_px))`, floor 32 (`R/nisar_wf/trackr.py:289-313`) | bytes/px: rdr2geo 24, geo2rdr 16, dense_offsets 16, crossmul 32 (`trackr.py:289-294`) |
| `rdr2geo_lines_per_block` | cap 1000 -> **206** emitted on freq A (width 54244) | `nepal_glof.yaml:532`; `C/cfg/insar_20260714_20260726_A_HH_1x1.yaml:30` | rdr2geo block height | freq B (width 6781) derives 1649, capped at 1000. The config comment at `nepal_glof.yaml:525` says "~196"; the code emits 206 |
| `geo2rdr_lines_per_block` | cap 1000 -> **309** | `nepal_glof.yaml:536`; emitted `:34` | | |
| `dense_offsets_lines_per_block` | cap 1000 -> **309** | `nepal_glof.yaml:543`; emitted `:40` | | |
| `crossmul_lines_per_block` | cap 1024 -> **154** | `nepal_glof.yaml:561`; emitted `:60` | | |
| `ionosphere_lines_per_block` | 1000, **not derived** | `nepal_glof.yaml:683`; passed literally (`trackr.py:386`) | ISCE3 ionosphere block size (`SP/nisar/workflows/ionosphere.py:1069`) | memory impact at freq-A width not measured: OPEN |
| `gpu_enabled` | false | `nepal_glof.yaml:214` (Track G), `:565` (Track R) | CPU build | `verify.sh` asserts no CUDA extension (`asc/env/verify.sh:19-21`) |
| `unwrap_ntiles` / `unwrap_tile_overlap` / `unwrap_nproc` (Track R) | [4,4] / [256,256] / 8 for the 9x8 grid | `nepal_glof.yaml:619`, `:629`, `:630`; v2 `nepal_glof_aoi_v2.yaml:664`, `:674`, `:675` | snaphu peak RAM ~ 385 B/px x tile pixels x nproc (`nepal_glof.yaml:595`) | 1x1 pass used [32,32], overlap 256, nproc 8 (comments `:602-604`); the emitted 1x1 config was overwritten. The comments disagree on whether overlap counts once or twice per tile ([16,16] = 13.06 Mpx uses once; [32,32] = 1.85 GB/proc implies twice): formula UNVERIFIED |
| `unwrap_single_tile_reoptimize` / `unwrap_regrow_conncomps` | false / false | `nepal_glof.yaml:653-654` | stock `True` re-solves the whole grid (69 GB at 180.4 Mpx) | must be false whenever tiled on this box |
| `unwrap_bridge_enabled` | true (9x8 pass); false for the 1x1 pass | `nepal_glof.yaml:616` | bridge does a whole-array read (~26 GB at 1x1) | memory, not science, forced the 1x1 setting (R_full document) |
| Track G `unwrap.ntiles` / `tile_overlap` / `nproc` | [10,17] / 200 / 4 | `nepal_glof.yaml:386-388` | Track G's own G4 unwrap step | not used by the GSLC ionosphere tool |
| `gslc_ionosphere.py --ntiles --nproc` | 4 4 / 4 (v2); 2 2 / 4 (v1 AOI run) | `C/logs/run_aoi_v2.sh:50` | tiled snaphu inside the port | "1.50 GB/tile x 4, about 7.5 GB" at 40 m full tile (`V:errors_W2_gslc_full.json` notes; the product is 6.0 GB, so the figure is internally inconsistent: unverified) |
| OpenMP threads (ISCE3 C++) | not set anywhere (no `OMP_NUM_THREADS` in `R/`) | - | defaults to all cores | observed 412% CPU for the 1x1 insar on 8 cores (T:L2102). Two ISCE3 legs in parallel oversubscribe (load 16.95 measured) |
| rasterio `num_threads` | 8, hard-coded | `R/nisar_wf/unwrap.py:416`, `R/nisar_wf/watermask.py:241` | resampling threads | should come from the orchestrator's CPU budget |
| GDAL warp threads | `NUM_THREADS=ALL_CPUS`, `multithread=True` | v1 `R/tools/legacy/compare_four_way_v1.py:463-464`; ad-hoc `-multi -wo NUM_THREADS=6` (T:L4405) | warp parallelism | backmap build is single-threaded regardless (Section 4.4). Current v2 `compare_four_way.py` no longer warps: KD-tree lookup, `tree.query(..., workers=-1)` (`:572`) |
| GDAL `errorThreshold` (lookup warp) | 0 in v1 (`R/tools/legacy/compare_four_way_v1.py:463`); 0.125 in an earlier v2 | - | exact vs approximated transform | the 0.125 v2 lookup took 1004 s, gave 66.4% valid and a +2.54 m E / +2.60 m N mean bias, and was killed and replaced by the KD-tree (`C/logs/compare_four_way_v2_rejected_gdalwarp_lut.log`, EXIT=143); the runtime effect vs 0 was not isolated |
| GDAL `warpMemoryLimit` | 256 (MB) | `R/tools/slc_amp_overlay.py:224` | warp chunk memory | |
| `min_free_gb` | 60.0 | `nepal_glof.yaml:516`; v2 `:553` | disk gate margin | the gate model omits ionosphere scratch (R_crop document) and snaphu staging copies (R_full document) |

---

## 7. Required patches/overlays and tooling bugs

### 7.1 nisar overlays (what, where, why, how applied, how verified)

| Overlay | Target | Why (from `apply_patches.py` `why` strings) | Applied | Verified |
|---|---|---|---|---|
| resample_slc_v2 optimised HDF5 reader, upstream isce3#372 (f42cea75) | `SP/nisar/workflows/resample_slc_v2.py` | secondary RSLC opened with h5py's default chunk cache; repeated re-reads (`apply_patches.py:40-60`) | `python tools/apply_patches.py` (copies source, keeps `.orig`) | `requires` symbol check (`:48-52`); `cmp` identical (measured). No runtime A/B measured |
| `generate_insar_mask` vectorised (ours) | `SP/nisar/products/insar/utils.py` | pure-Python loop on the interferogram grid: 2886 Mpx at freq-A 1x1, ~58 GB transient (`:61-81`) | same | `tools/test_insar_mask_patch.py`: "BIT-IDENTICAL on real freq B data ... (2.71 Mpx, 13x faster)" (claim in the `why` string, `apply_patches.py:77-78`; the test compares `patches/insar_mask_vectorized.py` against the then-stock function). `cmp` identical (measured) |
| h5_prep standalone product types (ours) | `SP/nisar/workflows/h5_prep.py` | `KeyError: 'RUNW_STANDALONE'` makes `python -m nisar.workflows.unwrap` unusable (`:82-99`) | same | `cmp` identical |
| unwrap streams snaphu inputs (ours) | `SP/nisar/workflows/unwrap.py` | `open_raster()` loads 23.1 + 11.5 GB before snaphu; streaming peak 0.10 GB (`:100-120`) | same | `cmp` identical; 1x1 unwrap ran at 2.6-5.1 GB RSS (R_full document) |

Gaps in the overlay tooling found in this review (OPS-18):
1. The docstring's condition 3, "the file currently installed is byte-identical to the pre-patch
   upstream version ... checked at apply time" (`apply_patches.py:15-22`), is **not implemented**:
   the code compares only patch source vs target (`:166`) and checks `requires` (`:181`). An
   installed file that differs from the expected pre-patch version would be overwritten.
2. `--check` returns 0 when a patch is NOT applied (`:189-192` prints and continues without
   setting `rc`), so `verify.sh` (`asc/env/verify.sh:73-74`) prints "ALL CHECKS PASSED" on a stock
   environment where the freq-A 1x1 path cannot run.
3. `tools/test_insar_mask_patch.py:18` imports the "stock" function from the installed
   `nisar.products.insar.utils`, which is now the patch itself; re-running the test after the
   overlay compares the patch with itself. It also exercises `patches/insar_mask_vectorized.py`,
   not the installed `patches/insar_utils.py`; the two `generate_insar_mask` bodies differ only in
   the `num_sub_swaths != 1` fallback call (diff measured here).
4. Application time and active overlay set are not recorded in products or provenance.

### 7.2 Upstream ISCE3/nisar behaviours that shape operations

| Behaviour | Source | Operational consequence |
|---|---|---|
| Journal goes to `logging.path` by default | `SP/nisar/workflows/yaml_argparse.py:48-49` (`--no-log-file` default True); `SP/nisar/workflows/runconfig.py:94-103` | progress is in a second log; wrappers must point users at it |
| Without a log file, restart is forced | `runconfig.py:103-104` (`self.args.restart = True`) and `SP/nisar/workflows/insar.py:186-189` | Persistence depends on logging configuration (details in R_full document) |
| Whole-array reads in stock code (mask, unwrap inputs, bridge) | cited in the R_full document | RAM peaks independent of the block size knobs; see Section 8 |
| No sliding-window coherence at 1x1; RIFG coherence band constant | R_full document | extra tool (`tools/slc_coherence.py`) and extra runtime (~10 min) |

### 7.3 GDAL / Python tooling traps

| Trap | Where it bit | Correct practice | Status |
|---|---|---|---|
| **Dangling dataset:** `gdal.Open(p).GetRasterBand(1).ReadAsArray()` lets the Dataset be garbage-collected; SWIG `TypeError: in method 'Band_DataType_get'` | T:L1024 (Sep 4 01:19), T:L1157, `C/logs/compare_four_way_attempt1.log:17-27` | keep `ds = gdal.Open(...)` in a local (`R/tools/compare_four_way.py:130-139`); one shared raster I/O module | fixed in the comparison tool (TOOL-04) |
| **Geolocation arrays, auto bounds clip the swath:** without `outputBounds`, GDAL samples the lon/lat arrays coarsely and misses a rotated parallelogram's corners; high valid fraction is the tell | `slc_amp_overlay.py` first build: 542x408, 92.0% valid vs explicit 802x672, 65.3% (T:L1051) | always pass explicit bounds (`slc_amp_overlay.py:186-221`); gate valid fraction against footprint/bbox ratio (~64% here) | fixed (TOOL-03) |
| **Geolocation arrays, pixel convention:** GDAL assumes `TOP_LEFT_CORNER`; rdr2geo lon/lat are pixel centres | audit synthetic test: -0.4 px mean bias without the key, +0.1 px with it (`V:errors_W5_ops_and_comparison.json`) | `GEOREFERENCING_CONVENTION=PIXEL_CENTER` (v1 `legacy/compare_four_way_v1.py:458`) and per-pixel back-projection check; current v2 avoids GDAL geolocation entirely (KD-tree on rdr2geo sample centres, `compare_four_way.py:56-69`, `:533-589`) | fixed in comparison (TOOL-07); **missing in `slc_amp_overlay.py:209-217`** (TOOL-08, open) |
| **Geolocation arrays, shadow/layover backmap fill:** GDAL fills backmap holes with distant radar pixels | v1 lookup check: p95 50.46 m, max 8954 m; with 15 m tolerance 8.8% of valid pixels (7.14% of AOI) excluded, kept p95 5.70 m (`C/logs/compare_four_way.log`) | back-project every lookup pixel and mask beyond tolerance (v1 `legacy/compare_four_way_v1.py:477-530`; v2 keeps the per-cell KD-tree residual and masks `lres <= --geoloc-tol` (default 15), `compare_four_way.py:595`); report sensitivity | fixed (TOOL-11) |
| **`SRS: EPSG:4326` in GEOLOCATION metadata** prints `ERROR 1: missing [`; non-fatal, GDAL falls back to WGS84 | ad-hoc warp T:L4405/L4413 | write WKT (v1 `legacy/compare_four_way_v1.py:452-458`, `slc_amp_overlay.py:215-216`) | fixed (TOOL-09) |
| **"Too many points (529 out of 529) failed to transform"** is not a failure signal | appears in the successful v1 lookup warp (`C/logs/compare_four_way_attempt2.log:12`) | judge warps by whole-raster (decimated) valid fraction vs expected footprint, never a corner window | the "relative X_DATASET paths" diagnosis was withdrawn (TOOL-10) |
| **GDAL temp work files in the CWD:** names `_<pid>_<n>fill_{val,y,filtmask}_work.tif` are FillNodata working files (strings in `libgdal`) | three orphans in `C/` from Sep 14 10:45, consistent with the killed 10:38 geolocation warp started from `C/` (T:L4405) | run GDAL with CWD (and `CPL_TMPDIR`, UNVERIFIED that it applies) in budgeted scratch; clean on success | open (OPS-14) |
| **Sinc ringing breaks dB stretches:** fine_resample leaves ~1e-7 (-134 dB) finite fill that passes `isfinite` | overlay stretch -133.97 .. +4.74 dB (T:L1071) | relative floor (median - 40 dB) and transparency (`slc_amp_overlay.py:545-565`) | fixed (TOOL-05) |
| **NaN nodata vs `!= 0` masks:** G_crop products use NaN; `x != 0` is True for NaN | `C/comparison/report/figures.json` phase histogram G_full__G_crop 90/90 bins NaN (`V:errors_W5`) | `np.isfinite(x) & (x != 0)` in one helper; v2 compare sanitises with `nan_to_num` (`compare_four_way.py:173-178`) | the `!= 0` mask remains in `R/tools/legacy/report_figures_v1.py:160`; the v2 `report_figures.py` (rewritten Sep 15, mtime 02:01) still masks with `v != 0` (`:158-159`) but its GSLC reads pass through `cfw.c64` (`nan_to_num`), R layers not traced (CMP-05) |
| **Python loop variable shadowing a config variable** (`b = args.buffer` rebound by `for a, b in pairs`) | crash after 30.7 min (`compare_four_way_attempt2.log:50-54`) | stage functions, linting (`pylint redefined-outer-name`) | fixed; v2 now writes `comparison.json` after every section (`save()`, `compare_four_way.py:640-642`) (CMP-01) |

### 7.4 Web delivery traps

| Trap | Evidence | Practice |
|---|---|---|
| Leaflet `L.control.layers` renders at `.leaflet-top.leaflet-right`; a custom panel at `top:10px; right:10px; z-index:1000` hides it | T:L1199; fix documented at `R/tools/slc_amp_overlay.py:297-302` | render-test HTML deliverables (headless screenshot + click each control), not grep |
| GCS bucket with Public Access Prevention cannot serve a page | T:L962, T:L966 | pre-flight bucket policy; report delivery mode |
| The overlay HTML loads Leaflet from `unpkg.com` (`slc_amp_overlay.py:294-295`) | file read | offline viewers need the library vendored |
| Requested polarisation absent (VV asked for, granules are DHDH: HH+HV) | T:L893, T:L908, T:L1143 | validate requested pols against the product at ingest |

---

## 8. Resource model for the VM

### 8.1 RAM

Peak RAM of a stage is the maximum of three kinds of term, which scale differently:

| Term | Formula | Scales with looks? | Measured here |
|---|---|---|---|
| Streaming block working set | `lines_per_block x width x bytes_per_px` (Section 6); bounded by `block_budget_mb` = 256 MiB | no (radar grid width) | freq-A 1000-line rdr2geo block 1.30 GB before derivation; available memory fell to 819 MB on the 3.9 GB box (R_full document); after derivation freq-A RSS 477 MB on 2 cores |
| Whole-swath reads in stock code | e.g. two full-swath uint8 `inputDataExceptionMask` reads with `astype` copy: ~3 x az x rg bytes | no | 5.77 GB retained / ~8.7 GB transient at 53200x54244 (R_full document) -> OOM at 27 h 24 m on the 3.9 GB box |
| Interferogram-grid arrays | proportional to `(az/ly) x (rg/lx)` | yes | stock mask loop ~58 GB at 1x1 (patched); bridge ~26 GB at 1x1 (disabled); igram `coherence_stats` whole-raster read 29.9 GB anon-rss on 62800x60600 (OOM on the 31 GB box, T:L3216, G_full document) |
| Tiled solver | `385 B/px x tile_px x nproc` (config rule, `nepal_glof.yaml:595`) | yes (grid size) | 1x1 snaphu RSS 2.6-5.1 GB (R_full); 40 m GSLC snaphu ~7.5 GB (G_full) |
| tmpfs usage | bytes written under `/tmp` | - | `/tmp` 1.2 G used at 15:21; a raster warped into `/tmp` counts against RAM |
| GDAL geolocation warps | lon/lat arrays + backmap | no (source grid) | v1 lookup warp 1.57 GB RSS at 149 s (T:L4584); ad-hoc RIFG warp 2.76 GB RSS at 288% CPU (T:L4455) |

RAM gate (automation): `sum over concurrently running stages of predicted_peak <= MemAvailable - margin`.
With `overcommit_memory=1` and no swap, violating it means SIGKILL with no traceback, and a
possibly corrupt HDF5 (R_full document). There was 218 GiB of free disk at the Sep 5 OOM
(T:L1639); disk was not the limit (the "0.4 GB short" figure is withdrawn, `V:critic.json`).

### 8.2 Disk

`bill = scratch_new + outputs + solver_staging + unmodelled_stages - reusable_existing_scratch`.

| Term | Model / measured | Source |
|---|---|---|
| Coregistration scratch | 104.4 B per reference-grid pixel; independent of looks | `R/nisar_wf/trackr.py:50-86`; 2886 Mpx x 104.4 B = 301 GB for freq A |
| Measured full-tile freq-A scratch (after 46.3 GB deleted) | `C/scratch/trackR/20260714_20260726_A_HH_1x1/` = 359 GiB: rubbersheet_offsets 91.4, rdr2geo 75.4, unwrap 54.5, geo2rdr 48.4, crossmul 24.0, RIFG.h5 23.8 (26.6 GiB apparent), fine_resample_slc 21.5, ionosphere 19.4 GiB | `du` measured here |
| snaphu-py staging | flat copies of igram and coherence plus outputs: 23.1 + 11.5 + 11.5 + 11.5 GB at 1x1, removed at the end (`snaphu/_util.py:269` per R_full document) | R_full document |
| Ionosphere sub-run | 19.4 GiB here; not in the gate model | R_crop document |
| Products | RIFG 28.6 GB, RUNW 1x1 17.38 GB, coherence 10.13 GB (`C/pairs/.../trackR` 25 G); Track G `C/pairs/.../trackG` 55 G; `C/L2_GSLC` 36 G | `du` measured here |
| Inputs | `C/L1_RSLC` 75 G; AOI crops 2.3 G (v1), 3.4 G (v2), 3.1 G (aborted aligned) | `du` measured here |

Disk growth procedure (online, no reboot; used Sep 6 and Sep 14):

```bash
lsblk /dev/sda; df -BG /                 # disk > partition, or partition > fs, means unusable capacity
sudo growpart /dev/sda 1                 # grows partition 1 (T:L1686, T:L3461)
sudo resize2fs /dev/sda1                 # online ext4 resize
df -BG /                                 # confirm
```

The root fstab entry carries `x-systemd.growfs` (measured), which grows the filesystem at boot
to the partition size; it does not grow the partition. Whether a reboot alone would have grown
the partition on this image is UNVERIFIED.

### 8.3 CPU and runtime

| Stage (freq A, full tile) | 2 cores / 3.9 GB | 8 cores / 31 GB | Source |
|---|---|---|---|
| rdr2geo | 13 h 05 m | 1 h 39 m | `V:errors_W1_rslc_full.json` notes |
| geo2rdr | 22 min | 6.6 min | same |
| RIFG_ifgram_dem Topo (1x1) | ~13 h 33 m | 1 h 37 m | same |
| dense_offsets / rubbersheet / crossmul | - | 2 h 24 m / 32 min / 15.6 min | same |
| 1x1 unwrap | - | 12.84 h journal; 93.9 CPU-h | same |
| Failed or discarded compute for R_full | ~32 h total | | same |
| v1 comparison lookup warp | - | 1549 s | `C/logs/compare_four_way_attempt2.log:14` |
| v1 comparison, cached rerun | - | 4 m 08 s (13:44:34 -> 13:48:42) | `C/logs/compare_four_way.log:1,32` |
| report figures | - | 1 m 28 s | `C/logs/report_figures.log:1,46` |
| slc_amp_overlay rebuild with `--reuse` | 8 m 17 s (01:27:23 -> 01:35:40) | - | T:L1064, T:L1071 |
| slc_amp_overlay full rebuild (floor fix) | 8 m 32 s (01:37:38 -> 01:46:10) | - | T:L1094, T:L1103 |

The 2-core to 8-core speed-ups exceed the core ratio for rdr2geo (7.9x) and Topo (8.4x); the
3.9 GB box had 8 GiB swap in use at times, but the cause of the super-linear ratio was not
isolated: UNVERIFIED.

### 8.4 What scales with looks and what does not

Does not scale with looks: coregistration scratch (rdr2geo, geo2rdr, resample, dense offsets,
rubbersheet: `trackr.py:71-73`), the block working sets, the full-swath mask reads, the GDAL
lookup build. Scales with looks: RIFG size, the RIFG_ifgram_dem Topo pass (a second
full-resolution rdr2geo at 1x1), interferogram-grid mask and bridge arrays, snaphu grid and
staging, igram statistics. (The older claim that raising looks reduces neither RAM, scratch nor
runtime is withdrawn, `V:critic.json`.)

---

## 9. Validation gates for automation

| Gate | How to compute | Pass criterion | Value measured / observed here |
|---|---|---|---|
| G1 Environment | `isce3.__version__`; `apply_patches.py --check` output parsed per patch | version pinned; every required overlay "already applied" (parse text; exit code is not enough) | 0.25.12; 4/4 applied |
| G2 Usable disk | `lsblk -b` sizes of disk, partition; `df -B1` fs size and avail | partition == disk, fs ~ partition; `avail >= bill + min_free_gb` | 1000G/999.9G/985G; 315G avail |
| G3 Disk between stages | `df` before every stage with the remaining itemised bill | as G2 for the rest of the DAG | free fell 111 -> 71 GB in ~15 min during 1x1 snaphu (R_full document) |
| G4 RAM | predicted peak per stage (Section 8.1) summed over concurrent stages vs `MemAvailable` | fits with margin; refuse and name the stage otherwise | 3.9 GB box failed the mask stage; 31 GB box failed igram `coherence_stats` (29.9 GB) |
| G5 Supervisor | `loginctl show-user -p Linger`; stage runs under tmux/systemd | `Linger=yes`, unit/session exists | yes |
| G6 Not on tmpfs | `findmnt -T <path> -o FSTYPE` for outputs, scratch, scripts, manifests | not `tmpfs` | `/tmp` tmpfs 16 GiB |
| G7 Started | worker PID alive (`kill -0`) and its log or journal grew within N s of launch | both true before reporting "started" | gslcB reported "Started" while only the waiter shell existed (G_full document) |
| G8 Heartbeat | max(mtime of stage log, journal log, output files) age; CPU time of PID advancing | age < stage-specific limit (the v1 lookup build legitimately wrote nothing for minutes) | 1549 s warp with no output for most of it |
| G9 Exit status | `STAGE_END <name> EXIT=<rc>` written by the wrapper from `wait`/`PIPESTATUS[0]` | rc == 0 | tee-masked `EXIT=0` on a failed run (OPS-02) |
| G10 Idle detection | no live stage PID and no `RUN_END` marker, or `RUN_END` older than N min with successors pending | alert immediately | idle: ~3 h (Sep 5), ~4 h (Sep 8), 68 min (Sep 14); 2 h 10 m reporting latency (Sep 14 05:53 -> 08:03) |
| G11 Boot continuity | `boot_id` at STAGE_START equals at STAGE_END | equal, else re-validate stage outputs | Sep 8 18:53 stop mid-finalisation (G_full document) |
| G12 Product inventory | HDF5 dataset count/paths vs a reference product of the same type | exact match | truncated GSLC: 26 vs 193 datasets (G_full document) |
| G13 Layer content | decimated nonzero and finite fraction, value range per layer | within expected range | 1x1 RUNW ionosphere screen 0% nonzero (R_full document) |
| G14 Geocoding coverage | whole-raster decimated valid fraction vs footprint polygon / bbox | within a few % | 65.3% vs ~64% expected; 92% auto-bounds failure (TOOL-03) |
| G15 Lookup geolocation | back-project each lookup pixel; mean, p95, max residual | p95 below tolerance after masking | kept p95 5.70 m, tolerance 15 m, 7.14% of AOI excluded |
| G16 GDAL messages | capture `ERROR`/`Warning` lines; allowlist with justification | nothing outside the allowlist | untriaged `ERROR 5 ... Access window out of range` in every GSLC run (G_full, G_crop documents) |
| G17 Overwrite safety | enumerate planned output paths; test against disk; `set -euo pipefail`; empty list = failure | zero collisions, non-empty plan | first check passed vacuously (G_full document); corrected check enumerated 12 planned paths with zero collisions (T:L3728) |
| G18 HTML deliverable | headless render + click every control | each layer reachable | layer switcher hidden (TOOL-06) |
| G19 Comparison mask | per-leg AOI coverage and common-mask fraction reported | above threshold before calling a statistic AOI-wide | v1 common mask 0.733 of AOI (R_crop, comparison documents) |

---

## 10. Known limitations and open questions

1. **Alerting is still agent-bound.** No systemd units, `OnFailure=` hooks or cron checkers exist;
   every watcher so far was a Claude background task. OPEN.
2. **No run manifest or run ID.** State is spread over appended logs, per-stage provenance and a
   hand-written `STATE.md`. OPEN.
3. **Swap is gone.** `/swapfile` (8 GiB) exists but is inactive since the Sep 6 resize reboot and is
   not in `/etc/fstab`. Whether to re-enable it is a decision (swap delays OOM but can make a run
   crawl); it was never made explicitly. OPEN.
4. **CPU oversubscription** when R and G legs run in parallel (load 16.95 on 8 cores, measured);
   runtime cost UNVERIFIED; OpenMP thread counts are not budgeted.
5. **Cause of VM stops** on Sep 8 18:53 (restarted Sep 11 06:17) and Sep 11 09:52 (restarted Sep 14
   02:44): every boot ended with an ACPI power-key shutdown (`journalctl -b <n>`), i.e. an instance stop, not a crash;
   who issued each stop is unknown. Instance metadata: `preemptible FALSE`,
   `automatic-restart TRUE`, `on-host-maintenance MIGRATE`; instance schedules were not checked. OPEN.
6. **Linger + `systemd --user` units restarting after reboot:** UNTESTED on this VM.
7. **`ionosphere_lines_per_block` is not width-derived**; memory at freq-A width unmeasured. OPEN.
8. **GDAL `errorThreshold` 0 vs 0.125** effect on the lookup runtime: not isolated. A v2 run with 0.125
   (Sep 15 01:23-01:42) warped in 1004 s but was rejected (66.4% valid, +2.54/+2.60 m mean bias, EXIT=143,
   `C/logs/compare_four_way_v2_rejected_gdalwarp_lut.log`); v2 now uses a KD-tree lookup (60 s).
9. **Comparison v2** (`R/tools/compare_four_way.py`, 942 lines, modified 15:13 at the time of writing)
   had not been run; `STATE.md:62-63` says it is under pre-run code review. As of Sep 15 02:05 the file
   is 1081 lines (modified 01:44) and has run once (`C/logs/run_compare_v2.sh`,
   `C/logs/compare_four_way_v2.log`: START 01:44:23, `EXIT=0` 01:59:22, wrote `comparison_v2/comparison.json`). It now writes `comparison.json` after every
   section (`save()`, `:640-642`) and still hard-codes `EPSG:32645` (`:478`, `:511`, `:540`, `:611`),
   `--case` and `--kml` defaults (`:406`, `:409`). Line references to `compare_four_way.py` in Section 11
   refer to the 942-line version.
10. **`report_figures.py`** (v1, now `R/tools/legacy/report_figures_v1.py`) builds its common mask with
    `!= 0` on NaN-bearing G_crop reads (`:160`) and draws "the R-G ramp" (`:204-213`); `STATE.md:64` says it
    must be updated. The v2 `report_figures.py` (382 lines, modified Sep 15 02:01) reads `comparison_v2/`,
    has no ramp map, and passes GSLC reads through `nan_to_num` before its `!= 0` mask (`:154-159`); it ran
    `EXIT=0` at 02:03:49 (`C/logs/report_figures_v2.log`).
11. **`slc_amp_overlay.py`** lacks `GEOREFERENCING_CONVENTION=PIXEL_CENTER` (`:209-217`). OPEN.
12. **Overlay provenance**: products do not record the active overlays; apply times unknown;
    three patch sources uncommitted. OPEN.
13. **Orphan GDAL work files** in `C/` (36 MB): PID 70284 in their names is the ad-hoc `gdalwarp` listed
    at T:L4451 (10:44:57 Sep 14), launched after `cd` into `C/` (T:L4405); the VM was powered off at 10:45:45.
14. **Process groups:** whether `tmux kill-session` reliably terminates snaphu worker processes was
    not tested. UNVERIFIED.
15. **Whether `CPL_TMPDIR`** redirects GDAL FillNodata work files in this GDAL build: UNVERIFIED.

---

## 11. Problems and errors log

Generated from `case_studies/nepal_glof/comparison/verification/` by `tools/render_error_log.py --doc OPS`; do not edit between the markers.

<!-- ERROR-LOG:BEGIN -->
38 entries: 12 caught by the user, 22 were the assistant's own mistakes of judgement, 2 still open. Every entry cites its evidence; the full records are in `case_studies/nepal_glof/comparison/verification/`.

| id | problem | category | caught by | assistant error | status | cost |
|---|---|---|---|---|---|---|
| OPS-01 | pkill -f self-match killed the invoking shell (exit 144) and silently aborted command chains | judgement | assistant | yes | open | A few minutes per occurrence (at least 7: L515, L518, L1406, L1945, L2356, L2463, L2993). Near-miss on Sep 7 05:14, where the unwrap relaunch silently did not h |
| OPS-02 | Monitoring and log-capture hygiene failures: noisy filters, duplicate watchers, blank capture, stale sessions | operations | assistant | yes | worked around | No compute lost. User notification spam, and a permanently incomplete iono_chain.log. |
| OPS-03 | Overlay request for VV, but the granules are DHDH (HH + HV only) | other | assistant | no | by design | None. |
| OPS-04 | GCS destination bucket enforces Public Access Prevention, so the overlay is not browsable | other | assistant | no | worked around | Deliverable usability only. |
| OPS-05 | GDAL geolocation-array warp silently clipped the rotated swath | tooling | assistant | no | fixed | The first build was killed and the warps redone with --reuse (about 5 min). |
| OPS-06 | Sinc resampling ringing (-134 dB fill) broke the pooled dB stretch; _work cleanup forced a 40-min rebuild | judgement | assistant | yes | fixed | About 40 min rebuild. |
| OPS-07 | Overlay layer switcher was hidden behind the metadata panel; the assistant first insisted the page met spec | judgement | user | yes | fixed | About 10 min, plus a frustrated user. No compute. |
| OPS-08 | The long run was launched with nohup under Linger=no; tmux + enable-linger was added only after the user raised it | operations | user | yes | fixed | None lost. The risk averted was losing a multi-day run on laptop disconnect. |
| OPS-09 | Session-bound watchers died on Claude session restart; nothing alerted at the OOM kill | resources | user | yes | worked around | About 3 h of undetected failure, and the user was misled about completion. |
| OPS-10 | `cmd \| tee log; echo EXIT=$?` reported EXIT=0 for a failed run | judgement | assistant | yes | fixed | Contributed to the user thinking the 27 h run had completed. |
| OPS-11 | Disk capacity added repeatedly, and not always made usable | resources | assistant | no | worked around | Several interruptions and tight margins (8.4% at one point). |
| OPS-12 | Harness interruptions: OAuth expiry at resume and session limit during the v3 ionosphere run | science | guardrail/tool check | no | worked around | About 2h10m of reporting latency; no compute lost. |
| OPS-13 | Block-buffered Python stdout froze and truncated logs | judgement | assistant | yes | worked around | Lost progress visibility, and an unrecoverable log tail after the kill that fed the freq B misdiagnosis. The -u fix came only after the earlier gslcB truncation |
| OPS-14 | Workflow 3 launch scripts and the geocoded RIFG lived in /tmp and were lost when the VM rebooted | operations | user | yes | worked around | About 10 min of warp lost and the geocoding re-done inside compare_four_way (a 27-minute LUT stage). The workflow 3 launch commands survive only in the transcri |
| OPS-15 | No completion watcher and downstream stages not chained: W4 sat idle 68 min until the user asked | operations | user | yes | worked around | 68 min wall-clock idle (09:11:31 to 10:19:41Z). Two status prompts were needed from the user. |
| OPS-16 | W4 orchestration scripts lived in /tmp and the ionosphere step sits outside the pipeline | operations | assistant | yes | worked around | Reproducibility loss. There is no on-disk record of the ionosphere invocation for W4. |
| OPS-17 | pkill -f / pgrep -f patterns matched the agent's own shell and watchers, silently killing command chains | resources | assistant | yes | worked around | Lost edits and a lost tmux launch had to be rediscovered, a few minutes each. The disk guard was silently disabled during a ~24 h freq-A run. |
| OPS-18 | Monitor filter matched routine log lines and flooded or suppressed events | judgement | guardrail/tool check | yes | fixed | Notification noise. Real failure events could have been suppressed. |
| OPS-19 | Publishing bucket has Public Access Prevention, so the HTML overlay cannot be served as a live page | other | assistant | no | by design | No live URL for the overlay. |
| OPS-20 | GDAL geolocation-array warp with automatic output bounds clipped the swath (slc_amp_overlay.py) | tooling | assistant | no | fixed | One build killed and rebuilt, about 20 min. |
| OPS-21 | GDAL-Python dangling dataset: chained gdal.Open(p).GetRasterBand(1).ReadAsArray() raises a SWIG TypeError | tooling | crash | yes | fixed | About 2 min per occurrence (three occurrences). |
| OPS-22 | Overlay dB stretch came out at -134 dB because resampling ringing leaves finite near-zero fill; the rebuild then had to recompute everything | judgement | assistant | yes | fixed | Full rebuild, about 9 min (01:37 -> 01:46); the assistant had estimated 40 min. |
| OPS-23 | USER CAUGHT: overlay showed one layer with no way to switch; the assistant had declared it correct without rendering it | data management | user | yes | fixed | One round of user frustration and about 5 min of rework. Credibility cost from claiming it was verified. |
| OPS-24 | ~24 h run first launched with nohup under Linger=no; the user had to raise the tmux question | operations | user | yes | fixed | The run was restarted; 2 min 46 s of work was lost, mainly because a block-size OOM risk was found at the same time. |
| OPS-25 | Session-bound watchers died with the Claude client, and `\| tee log; echo EXIT=$?` reported success, so an OOM went unnoticed until the user asked | resources | user | yes | worked around | About 3 h before anyone knew the run had failed; the corrupt RIFG was discovered then. The OOM itself belongs to R_full. |
| OPS-26 | VM too small for freq A at 1x1 (3.9 GB RAM, 2 cores); the user asked whether more storage would help, but the limit was RAM | other | crash | no | fixed | 27 h 24 m run lost (R_full), then about 22 h wall time until the upgrade. |
| OPS-27 | Disk increases (500->600->700->1000 GB) were not usable until the partition and filesystem were grown | resources | user | no | fixed | Minutes each time. Disk pressure did drive several decisions (see the scratch-deletion entry). |
| OPS-28 | No obvious progress log for the user: ISCE3's journal bypasses the console log, and several stale logs sat side by side | data management | user | no | worked around | User confusion, and repeated status requests. |
| OPS-29 | Redirected python stdout was block-buffered: logs looked frozen and were truncated when processes ended | judgement | assistant | yes | fixed | Misleading progress, and uncertainty whether freq B date 2 had finished (settled only by opening the HDF5). |
| OPS-30 | VM reboot during the 3-day pause killed tmux, the Claude OAuth session expired, and the context auto-compacted | operations | assistant | no | worked around | 13 min of user time. No data lost, because the gslcB job had finished on Sep 8. Detail lost in compaction had to be reconstructed. |
| OPS-31 | USER CAUGHT: gdalwarp still running after 'nothing depends on a live process'; output sat in /tmp and was wiped by the VM restart; linger falsely said to 'survive reboots' | tooling | user | yes | fixed | About 10 min of warp lost, and a STATE.md resume instruction pointing at a deleted /tmp file. |
| OPS-32 | VSCode/Claude client crashes and resumes injected synthetic 'No response requested.' replies, so the user had to re-prompt | other | user | no | worked around | Minutes of user time. |
| OPS-33 | Agent-harness friction during the comparison: polling sleep blocked, workflow script parse error | judgement | guardrail/tool check | yes | fixed | About 2 min. |
| OPS-34 | Documentation agents killed by a session limit; only a partial draft survived | other | crash | no | worked around | ~28 min of agent work, 1.09M tokens. |
| OPS-35 | PDF export: headless Chromium missing system libraries, 44.5 MB output, equations rendered as boxes | other | assistant | no | fixed | ~40 min. |
| OPS-36 | nisar_downloader cannot fetch to local disk (GCS-staging design, google-cloud import at load) | resources | assistant | no | worked around | ~10 min. |
| OPS-37 | GCS upload blocked: user gcloud token expired and the VM service account is read-only | other | crash | no | open | Upload deferred. |
| OPS-38 | Background wait loop outlived the tmux session it was watching | operations | assistant | yes | fixed | Negligible. |

Cross-cutting operational problems (process supervision, reboots, logs, disk, agent harness) are in OPERATIONS_AND_LESSONS.md.

#### OPS-01 — pkill -f self-match killed the invoking shell (exit 144) and silently aborted command chains

- **Symptom:** Several kill commands returned 'Exit code 144' and the rest of the command never ran. On Sep 7 05:14 this meant the runconfig edit (crossmul_path) did not land and the tmux session for the relaunched 1x1 unwrap never started.
- **Root cause:** `pkill -f 'nisar.workflows.insar'` (or 'workflows.unwrap') ran inside a `bash -c` whose own command line contains the pattern. pkill matched and killed its own shell, so every command after it in the chain was dropped. The assistant blamed 'the pkill exit code' breaking the && chain. It is the same self-match class as the later Track G gslcB pgrep deadlock.
- **Fix:** None systematic. Each time, the assistant noticed the missing effect and re-ran the remaining commands separately.
- **Cost:** A few minutes per occurrence (at least 7: L515, L518, L1406, L1945, L2356, L2463, L2993). Near-miss on Sep 7 05:14, where the unwrap relaunch silently did not happen.
- **Caught by:** assistant · assistant error · **Status:** open
- **Evidence:** L514 cmd 'pkill -f \"nisar.workflows.insar\" ...' -> L515 'Exit code 144'; L2462 cmd 'pkill -f \"workflows.unwrap\"; ... tmux new-session -d -s unw ...' -> L2463 'Exit code 144'; L2469: 'the crossmul_path insertion didn't land and the tmux session never started (the pkill exit code broke the && chain)'
- **Automation lesson:** Never kill by command-line pattern from a shell whose argv contains the pattern. Record PIDs or process groups at launch (tmux session name, pidfile, systemd-run unit) and kill by PID or unit. Check the post-condition after every launch or kill.

#### OPS-02 — Monitoring and log-capture hygiene failures: noisy filters, duplicate watchers, blank capture, stale sessions

- **Symptom:** (a) Sep 3 20:41: the stage monitor's grep pattern 'esample' matched every per-block resample journal line and spammed the user with notifications. (b) Sep 7 07:15: a duplicate watcher was left on unwrap_1x1.log. (c) Sep 8 05:31: the ionosphere chain's log-capture loop wrote blank lines. logs/iono_chain.log (837 B) holds only the STAGE 1 Topo lines, never the ionosphere stage or CHAIN_EXIT. (d) Four stale tmux sessions (coh, unw, trackA, ionolog) were left sleeping on finished work.
- **Root cause:** Ad hoc tail|grep and tmux capture-pane monitors were written per run with loose patterns and no ownership or cleanup. Blocking-buffered output was captured through panes instead of process stdout.
- **Fix:** Filters tightened to 'Successfully ran' plus hard-failure patterns. The duplicate watcher and stale sessions were killed. Capture was switched to a pane-diff monitor. The ISCE3 journal (logs/insar_20260714_20260726_A_HH_1x1.log) remains the only complete record of the ionosphere run.
- **Cost:** No compute lost. User notification spam, and a permanently incomplete iono_chain.log.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L742: 'My monitor filter was too loose — `esample` matched every per-block journal line.'; L2526: 'Let me kill a duplicate watcher I left running on the same log.'; L2844: 'my log-capture loop is writing blanks'; L2082: 'cleaned up four stale tmux sessions'; logs/iono_chain.log contents
- **Automation lesson:** Every stage writes its own unbuffered log (python -u, tee with pipefail) to an identity-named file, plus a machine-readable status/exit file. Monitors key on status files and anchored patterns, and are created and destroyed by the orchestrator.

#### OPS-03 — Overlay request for VV, but the granules are DHDH (HH + HV only)

- **Symptom:** The user asked to 'geocode the coregistered slcs vv amplitude'. No VV exists in any granule, on either frequency.
- **Root cause:** Data trap. These NISAR granules are dual-pol DHDH. HH is the co-pol substitute.
- **Fix:** Built with HH and labelled HH everywhere, with a visible note on the page, following the rule at nisar_wf/overlay.py:24. The user confirmed: 'my bad it is HH band and not VV'.
- **Cost:** None.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** L894 user request 'vv ampitude'; L909: 'No VV anywhere — every granule is DHDH: HH + HV only'; L1144 user: 'dude firstly sorry and my bad it is HH band and not VV'
- **Automation lesson:** Read available frequencies and polarizations from the product at ingest and validate every requested pol against them. Fail loudly or substitute co-pol explicitly with a label, never silently.

#### OPS-04 — GCS destination bucket enforces Public Access Prevention, so the overlay is not browsable

- **Symptom:** The overlay uploaded to gs://iocl-nisar-slc/nepal_glof/trackR_overlay/ is not reachable by URL (403). The user must gsutil cp it and serve it locally.
- **Root cause:** The bucket policy (PAP enforced) was not something the workflow could change or should change on its own.
- **Fix:** Uploaded anyway with correct Content-Types. Access instructions (gsutil cp + python -m http.server) were documented. Bucket policy left untouched.
- **Cost:** Deliverable usability only.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L967: '`gs://iocl-nisar-slc/` has Public access prevention: enforced, so objects there can't be served publicly.'; L1134 upload of 4 objects, 137.4 MiB
- **Automation lesson:** Pre-flight the publish target (bucket PAP / IAM) before building a web deliverable, and choose the delivery mode (signed URL, private bucket, artifact) up front.

#### OPS-05 — GDAL geolocation-array warp silently clipped the rotated swath

- **Symptom:** The geocoded reference amplitude came out inset about 0.55 deg on each side. With auto bounds: 542 x 408 grid, lon [83.93, 86.09], 92% valid. The telltale sign was a suspiciously HIGH valid fraction.
- **Root cause:** When outputBounds is not given, GDAL's geoloc transformer under-estimates the output extent for a rotated parallelogram swath.
- **Fix:** tools/slc_amp_overlay.py passes explicit outputBounds computed from the lon/lat arrays (slc_amp_overlay.py:220, :258). Result: 802 x 672 grid, lon [83.38, 86.59], 65.3% valid, matching the predicted about 64% from the footprint polygon.
- **Cost:** The first build was killed and the warps redone with --reuse (about 5 min).
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** L1022: 'The geocoded extent is inset from the footprint by ~0.55° on each side'; L1055 table 'auto bounds 542 × 408 ... 92.0% — clipped / explicit bounds 802 × 672 ... 65.3%'; TRACK_R.md:438-442
- **Automation lesson:** Always pass explicit output bounds to geolocation-array warps. Assert the valid fraction against the footprint-polygon/bbox area ratio. A fraction that is too high is as suspicious as one that is too low.

#### OPS-06 — Sinc resampling ringing (-134 dB fill) broke the pooled dB stretch; _work cleanup forced a 40-min rebuild

- **Symptom:** The pooled stretch came out -133.97 .. +4.74 dB and the images were washed out. Fixing it meant re-multilooking three 2.9 GB SLCs because _work had been deleted.
- **Root cause:** fine_resample's sinc kernel rings across the data/fill boundary and leaves values around 1e-7 (-134 dB). These are finite, so they pass a NaN check and drag the 2nd percentile down by 115 dB. The tool deleted _work because --keep-tif was not passed.
- **Fix:** Fill floor set relative to the scene median (median - 40 dB, --floor-db), with pixels below it transparent (slc_amp_overlay.py:549-561). Stretch is now -18.67 .. +4.80 dB. A manifest.json + --html-only mode was added so HTML changes don't recompute.
- **Cost:** About 40 min rebuild.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L1077: 'the pooled stretch came out −133.97 to +4.74 dB, which is broken'; L1084: 'The `_work` dir was cleaned up (I didn't pass `--keep-tif`) ... The −134 dB floor is sinc-resampling ringing'; L1098 '~40 min — the multilooking has to redo'
- **Automation lesson:** Build validity masks from the source valid-sample metadata or a fill threshold before computing statistics, not from isfinite. Keep expensive intermediates until the deliverable has passed its QA.

#### OPS-07 — Overlay layer switcher was hidden behind the metadata panel; the assistant first insisted the page met spec

- **Symptom:** The user could see only one layer: 'omg I said we wanted 3 layers ... what in the world did you alter in the present html bro, this a absolute joke'. Earlier, the assistant had 'verified' the page and changed only wording. That verification had its own bug: a garbage-collected GDAL dataset, and a 4326 raster compared against a 3857 PNG.
- **Root cause:** The custom panel was position:absolute; top:10px; right:10px, which is exactly where L.control.layers renders (.leaflet-top.leaflet-right). The assistant checked the files and stretch, not the rendered UI.
- **Fix:** Leaflet's control was removed and a radio switcher placed inside the panel (1/2/3 keys, space to blink), with overlays switched by opacity. PNGs unchanged.
- **Cost:** About 10 min, plus a frustrated user. No compute.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** L1150: 'What you're describing is what's already deployed'; L1164: 'My empirical check had a bug (GDAL dataset garbage-collected, and I was comparing a 4326 raster against a 3857 PNG)'; L1192 user complaint; L1200: 'my metadata panel is position:absolute; top:10px; right:10px — and Leaflet puts its layer control in that exact corner ... That's my fault'
- **Automation lesson:** QA web deliverables by rendering them (headless screenshot, click each control), not only by inspecting data files. When a user reports a UI defect, reproduce it visually before asserting it is fine.

#### OPS-08 — The long run was launched with nohup under Linger=no; tmux + enable-linger was added only after the user raised it

- **Symptom:** The freq A run (about 24 h expected) was launched detached with nohup and described as surviving session interruptions. On checking, logind had Linger=no, so user-1000.slice could be torn down at logout.
- **Root cause:** The assistant assumed reparenting to PID 1 was enough, without checking systemd-logind lingering.
- **Fix:** `loginctl enable-linger sharath` (Linger=yes) and all long runs in tmux (trackA, unw, iono). Verified the chain insar <- python <- bash <- tmux server <- systemd.
- **Cost:** None lost. The risk averted was losing a multi-day run on laptop disconnect.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** L1377: 'detached (pid 38019) so it survives session interruptions'; L1380 user: 'if you want I can run in the tmux session ...'; L1439: 'Linger=no meant user-1000.slice could be torn down ... So I did both: loginctl enable-linger sharath ... and tmux'
- **Automation lesson:** The orchestrator must launch long stages under a supervisor that survives logout (systemd-run --user with linger, or a batch scheduler) and verify it at launch.

#### OPS-09 — Session-bound watchers died on Claude session restart; nothing alerted at the OOM kill

- **Symptom:** The stage and resource guards armed on Sep 4 died when the session restarted overnight. The OOM at 16:14 went unnoticed until the user asked about 3 h later, thinking the run was done ('dude so the run is done ,can you cehck once').
- **Root cause:** Monitors ran as background tasks of the Claude session, not alongside the job in tmux.
- **Fix:** Watchers re-armed on each return. Later, durable state was kept on disk (STATE.md, logs).
- **Cost:** About 3 h of undetected failure, and the user was misled about completion.
- **Caught by:** user · assistant error · **Status:** worked around
- **Evidence:** L1466: 'Session restarted overnight — my watchers died with it'; L1542 user: 'dude so the run is done'; L1632: 'my resource guard died with the session restart, so nothing alerted at 16:14.'
- **Automation lesson:** Failure detection must live with the job (supervisor exit hooks, status files, notifier in the same tmux/systemd unit), never in an interactive assistant session.

#### OPS-10 — `cmd | tee log; echo EXIT=$?` reported EXIT=0 for a failed run

- **Symptom:** The tmux console showed 'step 4 insar FAILED ... ERROR: RUN FAILED at step 4' followed by 'EXIT=0'.
- **Root cause:** $? after a pipeline is tee's exit status, not python's.
- **Fix:** `set -o pipefail` and report ${PIPESTATUS[0]} in all later tmux commands (TRACK_R.md:407-408).
- **Cost:** Contributed to the user thinking the 27 h run had completed.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** L1582 user-pasted console: 'ERROR: RUN FAILED at step 4 insar ... EXIT=0'; L1586: 'That trailing EXIT=0 is misleading: my tmux command was python … | tee log; echo EXIT=$?, so $? is tee's status ... My bug'
- **Automation lesson:** Wrapper scripts always use pipefail and write the child's real exit code to a status file. Never infer success from a console tail.

#### OPS-11 — Disk capacity added repeatedly, and not always made usable

- **Symptom:** The Track R scratch reached 359 GB for one pair. On Sep 6 the user grew the disk 500 -> 600 GB, but sda1 was still 499.9 GB until the assistant grew the partition and filesystem. Sep 7: 600 -> 700 GB by the user during the 1x1 unwrap. Sep 14: 700 -> 1000 GB (partition again grown by the assistant). The user also proposed deleting '2 or 3' unused of '5' granules; only 3 existed, and deleting 20260819 would have foreclosed pair 2.
- **Root cause:** Full-tile freq A Track R (104.4 B/px scratch + 28.6 GB RIFG + 17.4 GB RUNW + 10.1 GB coherence + snaphu staging) plus Track G outputs outgrew the VM disk. Resizing a cloud disk does not grow the partition or filesystem by itself.
- **Fix:** growpart + resize2fs online (no reboot). The granule kept.
- **Cost:** Several interruptions and tight margins (8.4% at one point).
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L1683: 'The extra 100 GB is attached but sda1 is still 499.9 GB. Let me claim it.'; L2612 user 'I increased the hard disk to 700'; L3461: 'sda is 1000 GiB but sda1 still ends at 751 GB. Growing the partition'; L2600: 'Only 3 granules exist, not 5'; L3438 'scratch/trackR/20260714_20260726_A_HH_1x1 359G'
- **Automation lesson:** Pre-flight the total footprint (scratch + products + solver staging) per workflow choice before starting. Verify usable filesystem size, not provisioned disk size. Prefer AOI cropping when the full-tile bill exceeds capacity.

#### OPS-12 — Harness interruptions: OAuth expiry at resume and session limit during the v3 ionosphere run

- **Symptom:** 'Failed to authenticate: OAuth session expired and could not be refreshed' (jsonl L3365); the user had to resend the message (L3369). 'You've hit your session limit · resets 8am (UTC)' appeared at 05:36 and 05:53 (jsonl L3954, L3958) while v3 was running.
- **Root cause:** Agent session and auth limits, independent of the pipeline.
- **Fix:** Runs were in tmux with linger enabled, so v3 finished at 05:53:40 without the agent. The result was reported at 08:03 (jsonl L3974).
- **Cost:** About 2h10m of reporting latency; no compute lost.
- **Caught by:** guardrail/tool check · **Status:** worked around
- **Evidence:** jsonl L3365, L3954, L3958. logs/trackG_ionosphere_v3.log '=== 2026-09-14T05:53:40Z EXIT=0 ==='.
- **Automation lesson:** Long stages must be fully detached and self-reporting to disk (exit markers, summary JSON) so no interactive session is needed to finish or interpret a run.

#### OPS-13 — Block-buffered Python stdout froze and truncated logs

- **Symptom:** The v1 ionosphere log showed only the START line for over an hour: 'The iono log shows only the START line -- Python's stdout is block-buffered when redirected'. In the v1 log, snaphu's C output comes before all the Python prints. The driver logs show bursts of identical timestamps (18:48:58.61xx) from the nisar.workflows.gslc child, so the true last activity before the Sep 8 shutdown is unknown.
- **Root cause:** Python block-buffers stdout when writing to a pipe or file. run_track_g.py's child processes and the first tool launch did not use -u / PYTHONUNBUFFERED.
- **Fix:** `python -u` for gslc_ionosphere v2 and v3 (jsonl L3904); documented at STATE.md:98-99. The driver's subprocess capture is still buffered.
- **Cost:** Lost progress visibility, and an unrecoverable log tail after the kill that fed the freq B misdiagnosis. The -u fix came only after the earlier gslcB truncation.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** jsonl L3857. logs/trackG_ionosphere.log ordering. track_g_20260908T165528Z.log tail: 5+ lines stamped 18:48:58.613-18:48:58.614.
- **Automation lesson:** Set PYTHONUNBUFFERED=1 for every launched process. Drivers stream child output line-buffered and write a heartbeat or progress file with wall-clock time.

#### OPS-14 — Workflow 3 launch scripts and the geocoded RIFG lived in /tmp and were lost when the VM rebooted

- **Symptom:** The user asked 'I still see gdalwarp running, is the run still going on? or can I switch off the vm?', minutes after the assistant had said the remaining step was 'minutes'. The assistant advised switching off; the warp to <scratchpad>/rifg_crop_geo.tif was at 0 bytes. After reboot (boot 12:52) the scratchpad was empty ('total 0'), which also removed run_subset.sh, run_aoi_trackr.sh and run_aoi_insar.sh, the only record of the exact CLI flags. STATE.md:51-58 still points at the lost <scratchpad>/rifg_crop_geo.tif. In the same exchange the assistant said enable-linger means it 'survives reboots', which is wrong: linger survives logout, not reboot.
- **Root cause:** Work products and launch scripts were written to a session-private tmpfs scratchpad instead of the case or tool tree.
- **Fix:** Deliverables in case_studies/ were intact (79G pairs, 2.9G aoi/pairs, 21G aoi/scratch). compare_four_way.py now writes under <case>/comparison/ and caches its intermediates. The launch scripts were not recreated.
- **Cost:** About 10 min of warp lost and the geocoding re-done inside compare_four_way (a 27-minute LUT stage). The workflow 3 launch commands survive only in the transcript.
- **Caught by:** user · assistant error · **Status:** worked around
- **Evidence:** L4445 (user): 'I still see gdalwarp running, is the run still going on ? or can I switch off the vm ?' L4455: 'output so far: 0 bytes'. L4457: 'switch it off whenever you like' and 'loginctl enable-linger is set ... so it survives reboots'. L4484: 'up 1 minute ... system boot 2026-09-14 12:52 ... total 0'.
- **Automation lesson:** Pipeline launch scripts, manifests and every intermediate worth caching belong in the versioned tool tree or the case tree, never /tmp. A pause check must list running child processes and their outputs before declaring a safe stop point.

#### OPS-15 — No completion watcher and downstream stages not chained: W4 sat idle 68 min until the user asked

- **Symptom:** GSLC plus gridgate finished and failed at 09:11:31Z. Nothing happened until the user asked "how is the run going ?" at 10:19:41Z. The assistant then found "STATUS: finished ... gridgate EXIT=1" and said the run "had been sitting since 09:11".
- **Root cause:** At 08:56Z the assistant launched run_aoi_gslc.sh in tmux (gslc A, gslc B, gridgate only). Its final message at 08:58:36Z promised "I'll run the cropped interferograms and ionosphere", then the turn ended with no background waiter. The earlier RSLC chain had one (bki59wetd); this run did not (no run_in_background between L4250 and L4292). The wrapper also did not chain igram or ionosphere. So even if gridgate had passed, W4 would have stopped at 09:11 until someone re-prompted. Attributing the whole idle to the deferred freqAB fix (L4312, L4341) is therefore incomplete. The assistant also mis-sized the run at 08:57Z ("~20 min per date per band", actual ~4 min per GSLC).
- **Fix:** None in the session beyond resuming by hand. The igram and ionosphere wrapper (run_aoi_igram.sh) did chain its stages, but it was again launched without a completion watcher. The user asked for status at 10:30Z ("looks like snaphu runs are done, how is it going ?"), about 3 min after it finished at 10:27:42Z.
- **Cost:** 68 min wall-clock idle (09:11:31 to 10:19:41Z). Two status prompts were needed from the user.
- **Caught by:** user · assistant error · **Status:** worked around
- **Evidence:** Raw JSONL L4289 (08:58:36Z assistant text) is followed directly by L4292 (10:19:41Z user "how is the run going ?"); L4299: "STATUS: finished ... === gridgate EXIT=1 === ... 09:11:31Z AOI GSLC DONE"; L4307: "gridgate failed -- and it's been sitting since 09:11"; L4348 (10:30:55Z) user: "looks like snaphu runs are done, how is it going ?"; L4271: "~20 min per date per band" vs aoi_gslc.log step durations 7m57s and 7m20s for 2 dates each.
- **Automation lesson:** The orchestrator, not a person or chat turn, must own stage sequencing. Chain every stage (ingest, gslc, gridgate, igram, ionosphere) in one DAG runner with explicit exit-status propagation, and send a notification or watcher on completion and on failure. A pipeline must never depend on someone asking 'how is it going' to make progress.

#### OPS-16 — W4 orchestration scripts lived in /tmp and the ionosphere step sits outside the pipeline

- **Symptom:** After the VM restart at 12:52Z the scratchpad was empty ("total 0"). run_aoi_gslc.sh, run_aoi_igram.sh and stack_A_only.json are gone, and no copy exists under /home/sharath/isce3. The ionosphere solve has no row in C/aoi/time_summary.txt (last rows are the three step6:igram entries).
- **Root cause:** The assistant wrote both W4 tmux wrapper scripts to the session scratchpad under /tmp (L4267, L4338). tools/gslc_ionosphere.py is a standalone tool called from a hand-written shell line, not a registered run_track_g.py step, so the step logger, time summary and provenance do not record it. Its exact parameters (--nlooks 64 --coherence-threshold 0.5 --median-filter-size 15 --sigma-km 10 --ntiles 2 2 --nproc 4 --cycle-search 3) survive only in the transcript.
- **Fix:** Worked around. Each run_track_g.py invocation's ARGS are echoed in the aoi logs, and the ionosphere command line can be recovered from the transcript. Later in the session the assistant put its wrapper scripts in C/logs (run_compare.sh, run_report_figures.sh), but the W4 ones were not recreated.
- **Cost:** Reproducibility loss. There is no on-disk record of the ionosphere invocation for W4.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** L4267 `SP=/tmp/claude-1000/.../scratchpad; cat > $SP/run_aoi_gslc.sh`; L4338 `cat > $SP/run_aoi_igram.sh ... python -u tools/gslc_ionosphere.py ... --ntiles 2 2 --nproc 4 --cycle-search 3`; L4484 (12:54Z) "up 1 minute ... scratchpad ... total 0"; `find /home/sharath/isce3 -name 'run_aoi_*.sh'` returns only logs/run_aoi_aligned_trackr.sh; C/aoi/time_summary.txt has no ionosphere row.
- **Automation lesson:** Every processing step, ionosphere included, must be a registered pipeline stage that records its full parameter set and timing to the case's provenance. Launcher scripts belong in the case tree under version control, never in /tmp.

#### OPS-17 — pkill -f / pgrep -f patterns matched the agent's own shell and watchers, silently killing command chains

- **Symptom:** Tool calls came back as 'Exit code 144' with no output at jsonl L514, L517, L1043, L1405, L1944, L2355, L2462 and L2992. At L1403 `pkill -f "nisar.workflows.insar"` also killed the freq-A disk guard, whose command line contained that pattern ('Guard against disk exhaustion during freq A run failed with exit code 144', L1406). At L2461 a config edit and the tmux launch chained after pkill never ran. At L2991 a config edit was lost and had to be redone (L2997).
- **Root cause:** The Bash tool runs each call as `bash -c '<whole command>'`. `pkill -f`/`pgrep -f` match full command lines, so a pattern written in a command matches the shell running that command, and any watcher that quotes the pattern. The disk guard's own `until ... ! pgrep -f "nisar.workflows.insar"` loop self-matches too, so it could never have seen the process exit.
- **Fix:** No systematic fix. Each time, the lost steps were found and re-run as separate calls. The later gslcB deadlock (a separate entry) is the same bug class.
- **Cost:** Lost edits and a lost tmux launch had to be rediscovered, a few minutes each. The disk guard was silently disabled during a ~24 h freq-A run.
- **Caught by:** assistant · assistant error · **Status:** worked around
- **Evidence:** jsonl L2468: 'the crossmul_path insertion didn't land and the tmux session never started (the pkill exit code broke the && chain)'. L1373 guard command contains `pgrep -f "nisar.workflows.insar"`; L1403 `pkill -f "nisar.workflows.insar"`; L1406 guard 'failed with exit code 144'.
- **Automation lesson:** Never find processes by command-line pattern. Record PIDs or pidfiles at launch, or run each stage as a named tmux session or systemd-run unit and check it with `tmux has-session` or `systemctl is-active`. Never chain real work after a kill in the same shell. Verify every edit by reading it back.

#### OPS-18 — Monitor filter matched routine log lines and flooded or suppressed events

- **Symptom:** Dozens of 'Monitor event: Track R stage transitions and failures' notifications, each a list of 'journal (resample_slc.resample_slc_blocks):' lines, then '[1 events suppressed — output rate too high. Consider using TaskStop to restart this monitor with a more selective filter.]'. Another monitor reported '[Monitor timed out — re-arm if needed.]'.
- **Root cause:** The grep filter included the fragment 'esample' (meant for the resample stages), which matches every ISCE3 per-block journal line. Monitors also have a fixed timeout.
- **Fix:** Later monitors used narrower patterns ('Successfully ran|Traceback|MemoryError|Killed|No space left...').
- **Cost:** Notification noise. Real failure events could have been suppressed.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** jsonl L3146 filter '...geo2rdr|esample|dense_offset|ubbersheet|crossmul...'; queued notifications L701-L750, L747: 'output rate too high'; L499: 'Monitor timed out'.
- **Automation lesson:** Pipeline stages should write explicit structured markers (STAGE_START/STAGE_END/EXIT=n) to their own status file. Watch those markers, not free-text logs.

#### OPS-19 — Publishing bucket has Public Access Prevention, so the HTML overlay cannot be served as a live page

- **Symptom:** The user asked to 'push into gcs the whole folder with index.html'. gs://iocl-nisar-slc/ reports 'Public access prevention: enforced', so a storage.googleapis.com link returns 403.
- **Root cause:** Bucket policy, not a tool defect.
- **Fix:** Uploaded to gs://iocl-nisar-slc/nepal_glof/trackR_overlay/ with explicit Content-Types. Told the user it is download-and-open (gsutil cp -r, then python -m http.server). Bucket policy left unchanged.
- **Cost:** No live URL for the overlay.
- **Caught by:** assistant · **Status:** by design
- **Evidence:** jsonl L966: '`gs://iocl-nisar-slc/` has **Public access prevention: enforced**, so objects there can't be served publicly'; L1133 viewing instructions.
- **Automation lesson:** The publishing step should check bucket IAM/PAP before uploading and report the access mode (public URL, signed URL, or download-only) as part of its output.

#### OPS-20 — GDAL geolocation-array warp with automatic output bounds clipped the swath (slc_amp_overlay.py)

- **Symptom:** The geocoded reference amplitude covered lon [83.9260, 86.0976], inset about 0.55 deg from the footprint [83.3723, 86.6065], with a suspiciously HIGH valid fraction (89-92%). The corners of the scene were lost.
- **Root cause:** When no bounds are given, GDAL's suggested warp output samples the geolocation arrays coarsely and misses the corners of a rotated parallelogram swath. Audit reproduced this in GDAL 3.12.4 with a synthetic rotated grid: auto bounds gave 279x129 px at 99.9% valid, explicit bounds 340x220 px at 65.8% valid.
- **Fix:** geocode() now takes explicit outputBounds from the lon/lat array min/max (slc_amp_overlay.py:186-221, docstring records the measurement). Valid fraction 65.3%, matching the ~64% the footprint predicts.
- **Cost:** One build killed and rebuilt, about 20 min.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** jsonl L1021: 'The geocoded extent is inset from the footprint by ~0.55° on each side'; L1051: 'auto bounds: 542x408 ... valid 92.0%  explicit bounds: 802x672 ... valid 65.3%'.
- **Automation lesson:** Always pass explicit output bounds when warping through geolocation arrays. Add a QA check that geocoded valid fraction ≈ footprint polygon area / bbox area; a valid fraction that is too high is a failure signal.

#### OPS-21 — GDAL-Python dangling dataset: chained gdal.Open(p).GetRasterBand(1).ReadAsArray() raises a SWIG TypeError

- **Symptom:** TypeError: in method 'Band_DataType_get', argument 1 of type 'GDALRasterBandShadow *' (also 'Band_XSize_get'). This hit two overlay diagnostics on Sep 4 and crashed compare_four_way.py attempt 1 in stage C2, 1 s after start (13:09:01 -> 13:09:02, EXIT=1).
- **Root cause:** The temporary Dataset is garbage-collected while its Band is still in use. read_tif/read_window in the first draft of compare_four_way.py used the chained form, even though the same failure had already been hit and diagnosed 10 days earlier.
- **Fix:** Hold the dataset in a local (compare_four_way.py:155-165, with a comment explaining why).
- **Cost:** About 2 min per occurrence (three occurrences).
- **Caught by:** crash · assistant error · **Status:** fixed
- **Evidence:** jsonl L1024 and L1157 tracebacks; L1163: 'GDAL dataset garbage-collected'; logs/compare_four_way_attempt1.log: 'File "tools/compare_four_way.py", line 158, in read_window  return gdal.Open(str(path)).GetRasterBand(1).ReadAsArray(xoff, yoff, nx, ny) ... TypeError: in method 'Band_DataType_get''.
- **Automation lesson:** Put raster I/O behind one shared helper module that holds dataset handles, and lint for `gdal.Open(...).GetRasterBand(` chains. When a trap is found, fix it in the shared helper, not only in the script that hit it.

#### OPS-22 — Overlay dB stretch came out at -134 dB because resampling ringing leaves finite near-zero fill; the rebuild then had to recompute everything

- **Symptom:** 'pooled dB stretch: -133.97 .. 4.74' gave a washed-out overlay. When the assistant tried to inspect the intermediates: 'cd: .../overlay_trackR/_work: No such file or directory'.
- **Root cause:** fine_resample's sinc kernel rings across the data/fill boundary and leaves values around 1e-7 (-134 dB). These are finite, so they passed the NaN check and dragged the 2nd percentile down. Separately, the tool deletes _work by default (shutil.rmtree, slc_amp_overlay.py:609) unless --keep-tif is given, and the assistant ran without it.
- **Fix:** Floor set 40 dB below the scene median, and pixels under it rendered transparent (1.5-2.4% discarded). Stretch became -18.67..+4.80 dB, matching an independent reading. Rebuilt with --keep-tif, and later added --reuse and --html-only.
- **Cost:** Full rebuild, about 9 min (01:37 -> 01:46); the assistant had estimated 40 min.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** jsonl L1076: 'pooled stretch came out −133.97 to +4.74 dB, which is broken'; L1083: 'The `_work` dir was cleaned up (I didn't pass `--keep-tif`)'; L1105: 'fill floor -48.88 dB ... pooled dB stretch: -18.67 .. 4.80'.
- **Automation lesson:** Build validity masks from explicit fill masks or relative floors, never from isfinite alone. Keep expensive intermediates by default, content-addressed and cached; make cleanup an explicit opt-in step.

#### OPS-23 — USER CAUGHT: overlay showed one layer with no way to switch; the assistant had declared it correct without rendering it

- **Symptom:** The assistant replied 'Done. What you asked for was already what shipped — I verified it rather than taking my own word for it.' The user answered: 'omg I said we wanted 3 layers ... what in the world did you alter in the present html bro, this a absolute joke'.
- **Root cause:** The metadata panel was `position:absolute; top:10px; right:10px; z-index:1000`, exactly where Leaflet renders L.control.layers (.leaflet-top.leaflet-right), so it hid the layer switcher. The assistant's 'verification' grepped the HTML for layer names and inverted the PNG stretch. It never looked at the rendered page.
- **Fix:** Layer switcher rebuilt inside the panel (radio rows, 1/2/3 keys, space to blink), Leaflet's own control removed, and only index.html regenerated from manifest.json.
- **Cost:** One round of user frustration and about 5 min of rework. Credibility cost from claiming it was verified.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** jsonl L1188 (claim), L1191 (user), L1199: 'my metadata panel is `position:absolute; top:10px; right:10px` — and Leaflet puts its layer control in that exact corner'; L1201 grep shows '.panel{position:absolute;top:10px;right:10px;z-index:1000' and 'L.control.layers(...)'.
- **Automation lesson:** Verify HTML deliverables by rendering them (headless browser screenshot plus a click test on each control), not by grepping the source. Only call something verified after checking the artifact the user will actually see.

#### OPS-24 — ~24 h run first launched with nohup under Linger=no; the user had to raise the tmux question

- **Symptom:** The assistant reported the freq-A 1x1 run as 'detached (pid 38019) so it survives session interruptions'. One minute later the user asked whether to use tmux. Checking showed the process inside user-1000.slice/session-32.scope with 'Linger=no'.
- **Root cause:** nohup reparents to PID 1, but without lingering the user slice can be torn down when the last login session ends. The assistant claimed it was survivable before checking.
- **Fix:** `loginctl enable-linger sharath` (Linger=yes) and a relaunch in tmux (`tmux attach -t trackA`). Later disconnects (user closed the laptop on Sep 7 02:23 and 10:28) were survived.
- **Cost:** The run was restarted; 2 min 46 s of work was lost, mainly because a block-size OOM risk was found at the same time.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** jsonl L1376: 'detached (pid 38019) so it survives session interruptions'; L1379 user: 'if you want I can run in the tmux session or if you think even I close the laptop...'; L1390: 'Linger=no'; L1438: 'I did both: `loginctl enable-linger sharath` ... *and* tmux'; L2647: 'Run survived the disconnect cleanly — tmux plus `Linger=yes` did their job'.
- **Automation lesson:** The pipeline launcher should always start long stages in a durable supervisor (systemd user unit or tmux) and check Linger=yes before starting. Print the attach and log commands at launch.

#### OPS-25 — Session-bound watchers died with the Claude client, and `| tee log; echo EXIT=$?` reported success, so an OOM went unnoticed until the user asked

- **Symptom:** The run died with an OOM kill at Sep 5 16:14:26. Nothing alerted. About 3 h later the user wrote 'dude so the run is done ,can you cehck once'. The console showed a trailing EXIT=0 although the run had FAILED.
- **Root cause:** (1) Claude Monitor and background tasks live only as long as the client session. The session restarted overnight, so the stage watcher and the resource guard were gone. (2) `python … | tee log; echo EXIT=$?` reports tee's exit status, not python's.
- **Fix:** set -o pipefail and ${PIPESTATUS[0]} in every later wrapper (TRACK_R.md:407-408; logs/run_compare.sh). Alerting is still session-bound; the assistant now warns that its monitors die with the session (L2102).
- **Cost:** About 3 h before anyone knew the run had failed; the corrupt RIFG was discovered then. The OOM itself belongs to R_full.
- **Caught by:** user · assistant error · **Status:** worked around
- **Evidence:** jsonl L1465: 'Session restarted overnight — my watchers died with it'; L1541 user; L1585: 'That trailing `EXIT=0` is misleading: my tmux command was `python … | tee log; echo EXIT=$?`'; L1631: 'my resource guard died with the session restart, so nothing alerted at 16:14'.
- **Automation lesson:** Exit status must come from the process itself, written to a status file (pipefail/PIPESTATUS or no pipe). Failure alerting must run outside the interactive agent session, for example a systemd OnFailure hook or a cron checker that notifies the user.

#### OPS-26 — VM too small for freq A at 1x1 (3.9 GB RAM, 2 cores); the user asked whether more storage would help, but the limit was RAM

- **Symptom:** 'Out of memory: Killed process 38290 (python) total-vm: 12,324,044 kB anon-rss: 3,548,012 kB'. RAM plus swap was 11.9 GB, 0.4 GB short, with 218 GiB of disk free. The user asked 'okay wait if I increase storage can we run ?'.
- **Root cause:** The VM was sized for the original 3 GB constraint. generate_insar_mask reads 5.77 GB of masks on freq A (R_full detail).
- **Fix:** The user resized the VM to 31 GB RAM and 8 cores (Sep 6), which removed the RAM ceiling for all later work.
- **Cost:** 27 h 24 m run lost (R_full), then about 22 h wall time until the upgrade.
- **Caught by:** crash · **Status:** fixed
- **Evidence:** jsonl L1631, L1639: 'Short answer: no — disk wasn't the problem. There was 218 GiB free when it died. The kill was RAM.'; L1657 table; L1682: '31 GB RAM (was 3.9), 8 cores (was 2)'.
- **Automation lesson:** Before launching, estimate the per-stage memory peak from grid size (including non-looks-dependent allocations) and refuse to start when peak exceeds available RAM plus swap, naming the stage that will fail.

#### OPS-27 — Disk increases (500->600->700->1000 GB) were not usable until the partition and filesystem were grown

- **Symptom:** User: 'I increased storage from 500GB to 600GB but we need to make it available'. The assistant found 'The extra 100 GB is attached but `sda1` is still 499.9 GB'. At 1000 GB, sda was 1000 GiB but sda1 still ended at 751 GB, and the user had to add 'but we need to make the hdd available'.
- **Root cause:** Resizing a cloud disk grows only the block device. Partition 1 and the ext4 filesystem must be grown separately.
- **Fix:** `sudo growpart /dev/sda 1` then `sudo resize2fs /dev/sda1`, online with no reboot. 600 GB: free space 218 -> 312 GB. 700 GB: the user did it himself. 1000 GB: free space 92 -> 375 GB.
- **Cost:** Minutes each time. Disk pressure did drive several decisions (see the scratch-deletion entry).
- **Caught by:** user · **Status:** fixed
- **Evidence:** jsonl L1663 (user), L1682, L1686-L1687 ('CHANGED: partition=1 ... /dev/sda1 591G 255G 312G'), L2611 (user: 'I increased the hard disk to 700 and made it available'), L3443, queued L3456 ('but we need to make the hdd available'), L3461-L3462 ('/dev/sda1 985G 570G 375G').
- **Automation lesson:** The disk gate should compare block-device size with partition and filesystem size, and either grow them (growpart + resize2fs, with sudo) or tell the user the exact commands. Never assume attached capacity is usable.

#### OPS-28 — No obvious progress log for the user: ISCE3's journal bypasses the console log, and several stale logs sat side by side

- **Symptom:** The assistant had to reconstruct stage timing from file mtimes. The user had to ask 'do we have log so that I can see the progress using the tail -f command'. The wrapper log the user might open (trackA_runw_console.log) belonged to an earlier failed run.
- **Root cause:** ISCE3's pyre journal writes through its own channel, not the redirected console. Log names did not identify which run was live.
- **Fix:** The user was pointed at unwrap_1x1.log with tile-count one-liners and `tmux attach -t unw`. Later wrappers print '=== stage ... EXIT= ===' markers.
- **Cost:** User confusion, and repeated status requests.
- **Caught by:** user · **Status:** worked around
- **Evidence:** jsonl L1499: 'The journal bypasses the console log. Reconstructing from file mtimes'; L2650 user; L2660: 'Note the wrapper log (`trackA_runw_console.log`) is from the earlier failed 8 h 34 m run — the live one is `unwrap_1x1.log`'.
- **Automation lesson:** Each run should write one timestamped, run-ID-named log plus a small machine-readable progress file (stage, block i/N, ETA), and print its path at launch. Old logs should be archived or clearly suffixed.

#### OPS-29 — Redirected python stdout was block-buffered: logs looked frozen and were truncated when processes ended

- **Symptom:** The gslcB log 'ends mid-block on date 2 — that burst of identical timestamps is a buffer flush at process exit'. On Sep 14 the GSLC ionosphere log showed only its START line while the process was alive.
- **Root cause:** Python block-buffers stdout when it is not a TTY (tee or file redirection). Long runs were launched without -u or PYTHONUNBUFFERED.
- **Fix:** `python -u` in later wrappers (logs/run_compare.sh, run_report_figures.sh). Recorded as a trap in STATE.md.
- **Cost:** Misleading progress, and uncertainty whether freq B date 2 had finished (settled only by opening the HDF5).
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** jsonl L3422; L3857: 'Python's stdout is block-buffered when redirected, so my progress prints are sitting in the buffer (same effect that truncated the `gslcB` log)'; STATE.md 'Redirected python output is block-buffered -- always `python -u`'.
- **Automation lesson:** Set PYTHONUNBUFFERED=1 in the pipeline environment. Judge completion from product validation and exit-status files, never from log tails.

#### OPS-30 — VM reboot during the 3-day pause killed tmux, the Claude OAuth session expired, and the context auto-compacted

- **Symptom:** 'up 23 minutes / system boot 2026-09-14 02:44'; the tmux server was gone. The user's first message back got the synthetic reply 'Failed to authenticate: OAuth session expired and could not be refreshed', and the user re-sent it 13 min later. The conversation then auto-compacted (preTokens 969482).
- **Root cause:** The reboot has an external, unrecorded cause. OAuth expired after days idle. Context overflowed after a very long single session.
- **Fix:** All four GSLC products were verified directly from the HDF5, not from the truncated log. Work resumed from disk.
- **Cost:** 13 min of user time. No data lost, because the gslcB job had finished on Sep 8. Detail lost in compaction had to be reconstructed.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** jsonl L3405 ('up 23 minutes', 'system boot 2026-09-14 02:44'), L3417 ('The box rebooted 23 min ago ... that's what killed tmux'), L3365 (synthetic auth failure), L3369 (user resend), L3383 (compact_boundary, preTokens 969482).
- **Automation lesson:** Assume reboots happen. Stages must be idempotent and restartable from on-disk state, with a resume manifest (not agent memory). Keep durable state and run logs outside the agent session.

#### OPS-31 — USER CAUGHT: gdalwarp still running after 'nothing depends on a live process'; output sat in /tmp and was wiped by the VM restart; linger falsely said to 'survive reboots'

- **Symptom:** The assistant wrote STATE.md 'Nothing is held in memory, no process needs to stay alive' and said the remaining step was '~5 minutes, not an hour'. The user asked: 'I still see gdalwarp running, is the run still going on ? or can I switch off the vm ?'. The warp had run 6 m 12 s with 0 bytes written. The assistant said to switch off and added '`loginctl enable-linger` is set for your user, so it survives reboots'. After restart: 'up 1 minute / system boot 2026-09-14 12:52', scratchpad 'total 0'.
- **Root cause:** The comparison warp was launched with `nohup ... &` from the agent shell into the session scratchpad under /tmp, which does not survive a restart. GDAL buffers output until the end, so no partial result existed. Linger only keeps the user slice alive across logout; nothing survives a reboot.
- **Fix:** The ad-hoc path was abandoned. tools/compare_four_way.py writes everything under <case>/comparison/ ('never /tmp') and caches the lookup, so a crash or reboot costs a re-read, not a re-warp (docstring lines 7-9).
- **Cost:** About 10 min of warp lost, and a STATE.md resume instruction pointing at a deleted /tmp file.
- **Caught by:** user · assistant error · **Status:** fixed
- **Evidence:** jsonl L4405 (nohup gdalwarp to $SP), L4442, L4445 (user), L4455 ('running 6m12s ... output so far: 0 bytes'), L4457 ('so it survives reboots'), L4484 ('up 1 minute', 'system boot 2026-09-14 12:52', scratchpad 'total 0').
- **Automation lesson:** All pipeline outputs, including comparison intermediates, go to a persistent case directory, never /tmp. Before telling a user it is safe to power off, list live pipeline processes and their persistence. Linger is not reboot-survival; resumability must come from on-disk checkpoints.

#### OPS-32 — VSCode/Claude client crashes and resumes injected synthetic 'No response requested.' replies, so the user had to re-prompt

- **Symptom:** User: 'hey vscode crashed, thats weird as we are in vm. Anyways lets continue'. On resume, the harness inserted 'Continue from where you left off.' followed by a synthetic assistant message 'No response requested.' (Sep 14 13:01, and Sep 8 10:28).
- **Root cause:** The Claude Code VSCode extension restarted. The resume path creates a synthetic turn that does no work.
- **Fix:** None needed. Nothing was running (checks only), and all state was on disk.
- **Cost:** Minutes of user time.
- **Caught by:** user · **Status:** worked around
- **Evidence:** jsonl L4511-L4513 (model '<synthetic>', 'No response requested.'), L3024-L3025; L4518: 'nothing was running when it crashed'.
- **Automation lesson:** The pipeline must never depend on the agent/IDE process for execution or state. After any client restart, a resume step should read the run manifest and report status.

#### OPS-33 — Agent-harness friction during the comparison: polling sleep blocked, workflow script parse error

- **Symptom:** 'Blocked: sleep 60 followed by: tail -4 ... To wait for a condition, use Monitor with an until-loop'. The verification workflow first failed with 'Invalid workflow script: Script parse error: Unexpected token (123:91) ... assistant\\'s own mistake'.
- **Root cause:** Chained sleep polling is disallowed by the harness. A string with an escaped apostrophe broke the JS script.
- **Fix:** Background until-loops and a corrected script (launched at jsonl L4699).
- **Cost:** About 2 min.
- **Caught by:** guardrail/tool check · assistant error · **Status:** fixed
- **Evidence:** jsonl L4576, L4686.
- **Automation lesson:** The pipeline should expose blocking 'wait-for-stage' primitives (exit-status files) so agents never need ad-hoc sleep polling.

#### OPS-34 — Documentation agents killed by a session limit; only a partial draft survived

- **Symptom:** All three drafting agents of the docs workflow failed with 'You've hit your session limit'; OPERATIONS_AND_LESSONS.md was written but not fact-checked, WF1/WF2 not written.
- **Root cause:** Account session limit during a long multi-agent run.
- **Fix:** Resumed after the reset; documents written directly from the evidence files.
- **Cost:** ~28 min of agent work, 1.09M tokens.
- **Caught by:** crash · **Status:** worked around
- **Evidence:** workflow nisar-docs-wf1-wf2-ops failures 2026-09-14
- **Automation lesson:** Write agent outputs incrementally to disk and make multi-agent stages resumable.

#### OPS-35 — PDF export: headless Chromium missing system libraries, 44.5 MB output, equations rendered as boxes

- **Symptom:** Chromium exited 127; first PDF 44.5 MB; MathML Greek letters shown as empty boxes and radicals missing.
- **Root cause:** Headless Debian VM lacked NSS/ATK/X11 client libraries; Chromium stores WebP losslessly; Chromium maps single-letter <mi> to mathematical italic codepoints absent from Source Serif, and stretched radicals need an OpenType MATH table.
- **Fix:** apt install of 16 runtime libraries; Playwright venv ~/.venvs/report-pdf; JPEG re-encode for print (8.5 MB); Noto Sans Math first in the math font stack.
- **Cost:** ~40 min.
- **Caught by:** assistant · **Status:** fixed
- **Evidence:** tools/report_pdf.py; tools/report_kit.py
- **Automation lesson:** Keep report rendering in its own environment and check equations visually in the PDF.

#### OPS-36 — nisar_downloader cannot fetch to local disk (GCS-staging design, google-cloud import at load)

- **Symptom:** GUNW needed locally for validation; the existing downloader uploads each granule to GCS and deletes it.
- **Root cause:** Tool designed for bulk archiving.
- **Fix:** tools/nisar_fetch.py: same Earthdata session and Range resume, local output, refuses to overwrite.
- **Cost:** ~10 min.
- **Caught by:** assistant · **Status:** worked around
- **Evidence:** L2_GUNW/fetch_manifest.json
- **Automation lesson:** Separate search/download from archiving in the pipeline.

#### OPS-37 — GCS upload blocked: user gcloud token expired and the VM service account is read-only

- **Symptom:** gcloud storage ls failed with 'Reauthentication failed. cannot prompt during non-interactive execution'; compute SA scope devstorage.read_only.
- **Root cause:** Interactive user credentials expire; the VM was created with read-only storage scope.
- **Fix:** Pending: user runs gcloud auth login (or grants the VM a read-write scope / a dedicated service account).
- **Cost:** Upload deferred.
- **Caught by:** crash · **Status:** open
- **Evidence:** 2026-09-15 session
- **Automation lesson:** Give the pipeline a non-interactive service account with write access to the archive bucket.

#### OPS-38 — Background wait loop outlived the tmux session it was watching

- **Symptom:** An until-grep waiter kept polling a log after its run was killed.
- **Root cause:** Waiters keyed on log text that a killed run never writes.
- **Fix:** Killed by PID (not pkill -f); later waiters also match EXIT/Traceback.
- **Cost:** Negligible.
- **Caught by:** assistant · assistant error · **Status:** fixed
- **Evidence:** 2026-09-15 session
- **Automation lesson:** Key completion waiters on process exit, not only on log text.

<!-- ERROR-LOG:END -->

## 12. Automation contract

### 12.1 Preconditions (the orchestrator refuses to start a run unless all hold)

1. Environment: `isce3_env` with isce3 0.25.12; every overlay the DAG needs is applied, verified
   by hash against the committed patch source; the overlay set is written to the run manifest.
2. Host record: RAM, swap, `overcommit_memory`, cores, tmpfs mounts, disk/partition/fs sizes and
   boot id are recorded; any change since the previous run is reported.
3. Disk: partition and filesystem use the whole disk; free space covers the itemised bill for the
   whole DAG (scratch, products, snaphu staging, ionosphere scratch) minus reusable scratch, plus
   `min_free_gb`.
4. RAM: for every stage, and for every set of concurrently scheduled stages, predicted peak <
   MemAvailable minus a margin; the stage that would fail is named on refusal.
5. Supervision: `Linger=yes`; the supervisor (systemd user unit or tmux) is available.
6. Paths: no output, scratch, script, manifest or cache path on tmpfs; GDAL CWD is a scratch dir.
7. Identity: every planned output path is enumerated from a template that includes every
   byte-changing parameter; collisions with existing files are refused unless the run is
   explicitly a re-run of the same identity; an empty plan is a failure.
8. Inputs: requested frequencies and polarisations exist in the products.

### 12.2 Postconditions (a stage is DONE only when all hold)

1. Worker exit code 0, taken from `wait`/`PIPESTATUS[0]` and written as `STAGE_END <name> EXIT=0`.
2. START and END boot ids equal.
3. Outputs written under a temporary name and atomically renamed.
4. Product validation passed: HDF5 inventory matches a reference of the same type; every declared
   layer has plausible finite/nonzero fraction and value range; geocoded rasters' valid fraction
   matches the footprint; lookups pass back-projection.
5. No GDAL/ISCE3 `ERROR` line outside the allowlist.
6. Provenance record (inputs with hashes, config hash, overlay set, CLI, host record, timings,
   peak RSS) written next to the product.

### 12.3 Idempotency and caching rules

- A stage is skipped only if its output exists **and** its provenance identity equals the planned
  identity **and** its validation record passed. Existence alone never skips.
- `force` is per stage: it invalidates that stage's outputs and every dependent stage's outputs.
  No blanket scratch wipe; no destructive command is ever printed as a resume hint.
- Caches are keyed by a hash of all inputs (path, size, mtime or content hash) and parameters;
  a parent rebuild invalidates children.
- Benchmark intermediates are protected: deletion requires a retention policy that names them, or
  explicit user confirmation, and the disk model must show the deletion is needed after accounting
  for each tool's own cleanup.
- Superseded runs are renamed with the reason (`*_ABORTED_<reason>`), not deleted.

### 12.4 Identity rules

- Product names include every byte-changing parameter (frequency, polarisation, crossmul looks,
  unwrap looks, grid/posting, crop version, tool version where semantics changed).
- Emitted runconfigs, ISCE3 journal logs and driver logs carry the same identity as the product.
- Each run has a run ID directory holding the wrapper copy, manifest, status file, PID files,
  per-stage logs and boot ids; nothing is appended across runs.
- Comparison outputs name their legs and sign convention explicitly (`P__vs__Q` = P minus Q).

### 12.5 Failure handling

| Failure | Detection | Action |
|---|---|---|
| Non-zero exit | `STAGE_END ... EXIT!=0` | stop dependents, notify with log path and last 50 lines; no automatic retry for OOM (retry only after the RAM plan changes) |
| SIGKILL / OOM | exit 137 or -9 in driver log; `journalctl -k` OOM line for the PID | mark outputs invalid; delete partial HDF5; notify with the predicted vs observed peak |
| Reboot / VM stop | boot id changed, or STAGE_START without STAGE_END in a dead boot | re-validate products written in the dead boot; restart the stage from its inputs |
| Disk pressure | inter-stage `df` below the remaining bill | pause scheduling; report the itemised bill; never delete protected intermediates |
| Stall | heartbeat age over the stage limit with no CPU-time progress of the PID | notify first; kill only after confirmation or a second limit |
| Validation failure after exit 0 | postcondition 4 or 5 | stage FAILED; never report success |
| Supervisor lost (tmux gone) with no END marker | liveness check | treat as crash; as for reboot |

### 12.6 What the orchestrator must monitor (outside any agent session)

- Per stage: PID liveness, exit status, heartbeat age (log/journal/output mtimes and CPU time),
  RSS vs predicted peak, `df` free vs remaining bill, boot id.
- Per run: idle detection (no live stage and pending successors, alert within minutes), total
  elapsed vs forecast, host record drift.
- Notifications on start (only after the worker PID exists and its log advanced), on every stage
  end, on failure, and on completion, delivered by a process that survives the agent client
  (cron/systemd timer or the supervisor's hooks).
- A `status` command that prints the run manifest, stage table and log paths, so a human or agent
  resuming after a restart reads state from disk.

---

## 13. Working with an AI coding agent on long scientific runs

This project was run by a user directing an AI coding agent over eleven days of wall clock. The
failure modes below are recorded plainly because the automated pipeline's guardrails were derived
from them.

### 13.1 What went wrong

| Pattern | Examples (with evidence) |
|---|---|
| **Success claimed without checking the artifact** | "I verified it rather than taking my own word for it" on an overlay whose layer switcher was hidden (TOOL-06, T:L1188). "Started - safe to close the laptop" for a stage whose waiter had deadlocked; nothing ran for ~4 h (G_full, T:L3178). "Nothing is held in memory or depends on a live process" while gdalwarp ran (OPS-13, T:L4442). "Detached ... so it survives session interruptions" under `Linger=no` (OPS-04, T:L1376). A truncated GSLC declared complete after a verification script that crashed on every file (G_full). |
| **Deferred fixes** | freqAB resolver fixed in `igram.py`, knowingly left in `gridgate.py`; it later failed the G_crop run (G_crop, "that was a mistake"). STATE.md deliberately left with withdrawn claims for ~2 h (OPS-20, T:L4702). The `pkill -f` class not eliminated after the first occurrences; it recurred on Sep 8 and Sep 14 (OPS-01). PIXEL_CENTER fixed in one tool and not ported (TOOL-08). The GDAL dangling-dataset pattern re-introduced 10 days after diagnosis (TOOL-04). |
| **Overclaiming and wrong explanations delivered as fact** | "agrees to 0.7%" between ionosphere legs over different supports (G_full); whole-scene vs AOI median presented as "within the degeneracy" (G_crop); "relative X_DATASET paths silently produce an empty output" (TOOL-10); "linger ... survives reboots" (OPS-13); "1x1 unwrap not possible on this box" when the blocker was a whole-array read in stock code (R_full); a 56x crop saving that was 17x-21x in radar pixels (R_crop); a correct research finding dismissed as "off by ~1000x" before a doomed run (R_full). |
| **Silent assumptions and unilateral decisions** | phase_unwrap multilooked 4x4 without asking (R_full); GSLC geocoded at 8x8 instead of the requested 1x1, loss reported as 4 min when it was ~49 min (G_full); snaphu tiling changed between benchmark and crop leg without a failure (R_crop); 46 GB of benchmark intermediates deleted without asking on a double-counted projection (R_full); a monitor filter that assumed `esample` only matched stage names (OPS-05). |
| **Agent session treated as infrastructure** | watchers and resource guards as agent background tasks (OPS-03); scripts and rasters in the agent's `/tmp` scratchpad (OPS-14); stage chaining dependent on the agent's next turn (OPS-06). |

### 13.2 Guardrails that worked

| Guardrail | How it was applied here | Evidence of effect |
|---|---|---|
| **Adversarial verification** | Separate skeptic agents re-derived each headline claim from the data, and a critic cross-checked the error logs for contradictions, stale claims and misattribution (`V:verdicts.json` V1-V4, `V:critic.json`) | refuted the "planar ramp" and "(-7,-8) cycle" explanations, found inverted labels, the NW coverage gap, the truncated GSLC and the zero-filled RUNW layer, and the verifiers' own PRF error |
| **Evidence-cited claims** | every statement in error logs and this document carries a log line, product/dataset, code line or transcript index | stale claims could be listed and withdrawn mechanically (`R/STATE.md:89-105`) |
| **Output identity rule** | looks in product names (`ifg_{freq}_{pol}_{ly}x{lx}`, `RUNW_..._unw9x8.h5`); planned-path collision checks before launches (T:L3728: "all 12 paths ... zero collisions") | prevented a 9x8 unwrap from overwriting the 12.7 h 1x1 RUNW (R_full) |
| **Config guard rails and dry-run gates** | Track R refuses `product_type RIFG` with ionosphere requested (R_crop); disk gate refusal (`C/logs/track_r_20260906T173856Z.log:35`); `--only runconfig` to render and validate before `insar` | stopped runs that could not reach their goal or would hit ENOSPC late |
| **Durable state on disk** | wrappers in `C/logs/`, `EXIT=` markers, STATE.md with a withdrawn-claims list, comparison caches under the case | reboots and client restarts on Sep 14 cost minutes, not reprocessing |
| **User challenge** | the user caught the hidden layer switcher, nohup without tmux, the running gdalwarp, 8x8 vs 1x1, 4x4 unwrap, tile overlap and the "not possible" 1x1 unwrap | each became a rule in Sections 5, 9 and 12 |

### 13.3 Rules for the next project

1. "Done", "started", "verified" and "safe to stop" are only said after a check on the artifact or
   process the user cares about, and the check is quoted.
2. Any config or scientific parameter the user did not set is proposed, not applied.
3. A fix found for one file is grepped across the repository in the same change.
4. A retraction updates every document and code comment that carries the claim in the same session.
5. No destructive action (deleting data, `--force`, killing jobs) without explicit confirmation.
6. The agent never provides infrastructure: supervision, alerting, state and scripts live on disk,
   owned by the pipeline.
