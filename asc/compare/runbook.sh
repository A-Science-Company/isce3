#!/usr/bin/env bash
# runbook.sh -- Venezuela T162 F007 two-track comparison, in order.
# Nothing here runs a workflow for you; it is the sequence + the exact
# invocations, so the harness is reproducible.
set -euo pipefail

ENV=/home/sharath/miniconda3/envs/isce3_env
export PROJ_DATA=$ENV/share/proj PROJ_LIB=$PROJ_DATA   # gdal/osr need this
PY=$ENV/bin/python
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA=/home/sharath/Desktop/work/isce3/case_studies/venezuela_t162_asc
REF=$DATA/NISAR_L1_PR_RSLC_022_162_A_007_4005_DHDH_A_20260613T100656_20260613T100731_P05023_N_F_J_001.h5
SEC=$DATA/NISAR_L1_PR_RSLC_023_162_A_007_4005_DHDH_A_20260625T100655_20260625T100730_P05023_N_F_J_002.h5
WORK=${1:-$DATA/compare_run}
mkdir -p "$WORK"

# ---------------------------------------------------------------- 0. predict
# Print every expected value BEFORE looking at any output. Keep it.
$PY "$HERE/expected.py" | tee "$WORK/expected.txt"

# ------------------------------------------------------------ 0b. self-test
$PY "$HERE/test_metrics.py"

# ------------------------------------------------------- 1. pin ONE geogrid
# --posting 5  : GSLC + GUNW wrapped igram (near native 5.4 m az / 5.7 m rg)
# --coarse 50  : comparison posting; 10x10 boxcar on 5 m -> N_eff ~82,
#                which matches Track 2's 11x11 crossmul looks (N_eff 83.7).
# --aoi        : 60 x 60 km sub-frame. REQUIRED for freq A: the full frame
#                needs 115 GB of rdr2geo+geo2rdr scratch and only 94 GB is free.
AOI="560000 1230000 620000 1170000"          # X0 Y1 X1 Y0, UTM19N metres
$PY "$HERE/common_grid.py" --rslc "$REF" --epsg 32619 \
    --posting 5 --coarse 50 --aoi $AOI --out "$WORK/gridpins"

echo
echo ">> paste $WORK/gridpins/geocode_block.gslc.yaml  into BOTH GSLC runconfigs"
echo ">> paste $WORK/gridpins/geocode_block.insar.yaml into the InSAR runconfig"
echo ">> and make these match between the two tracks or the comparison is void:"
echo "     dynamic_ancillary_file_group.dem_file       <- same file"
echo "     dynamic_ancillary_file_group.orbit_file     <- same file (or both none)"
echo "     dynamic_ancillary_file_group.tec_file       <- SAME, or NEITHER"
echo "     processing.correction_luts.solid_earth_tides_enabled (GSLC)"
echo "       vs processing.ionosphere_phase_correction.enabled  (InSAR)"
echo "     input_subset.list_of_frequencies            <- same freq + pol"
echo

# ----------------------------------------------------------- 2. run track 1
# nisar_gslc.py <ref.yaml>   ; nisar_gslc.py <sec.yaml>
# then form the interferogram on the map grid:
#   10 x 10 boxcar of 5 m cells -> 50 m, N_eff ~82
$PY "$HERE/gslc_igram.py" --ref "$WORK/ref.gslc.h5" --sec "$WORK/sec.gslc.h5" \
    --freq A --pol HH --looks 10 10 --out "$WORK/t1_50m"

# ----------------------------------------------------------- 3. run track 2
# nisar_insar.py <insar.yaml>   (keep scratch_path: offset_qc.py needs it)

# ------------------------------------------------------- 4. offset diagnostics
$PY "$HERE/offset_qc.py" --scratch "$WORK/scratch" --freq A --pol HH \
    | tee "$WORK/offset_qc.txt"
$PY "$HERE/offset_qc.py" --gslc "$WORK/ref.gslc.h5" "$WORK/sec.gslc.h5" \
    --pol HH --step 512 | tee -a "$WORK/offset_qc.txt"

# --------------------------------------------------------------- 5. compare
$PY "$HERE/compare_tracks.py" \
    --t1 "$WORK/t1_50m" \
    --t2 "$WORK/GUNW.h5" --t2-freq A --t2-pol HH --t2-layer wrapped \
    --posting 50 --neff 84 --coh-min 0.3 \
    --water "$WORK/water_50m.tif" --dem "$WORK/dem_50m.tif" \
    --inc "$WORK/incidence_50m.tif" \
    --report "$WORK/compare_report.txt"
