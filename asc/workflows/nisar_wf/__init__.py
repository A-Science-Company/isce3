"""
nisar_wf -- modular NISAR L1 RSLC -> L2 GSLC workflow package (Track G).

Stages implemented here
-----------------------
  A   ingest    : read RSLC metadata, write stack.json + PINNED geogrid
  B   dem       : stage an ellipsoidal-height DEM covering the AOI
  G1  gslc      : render + validate a gslc runconfig per date, run nisar.workflows.gslc
  G2  gridgate  : hard assert the two GSLCs are pixel-aligned
  QA  qa        : decimated-read quicklooks (never loads a full raster)

Stages deliberately NOT implemented yet (see README "Slotting in the rest"):
  G3  ifg       : conjugate product + coherence from two GSLCs
      unwrap    : phase unwrapping
      dolphin    : phase linking / time series

Every stage is independently runnable, idempotent, and writes a JSON provenance
sidecar next to its outputs. `stack.json` is the single source of truth that all
downstream stages consume -- in particular the pinned geogrid, which is what
makes two dates land on byte-identical grids.
"""

__version__ = "0.1.0"

__all__ = [
    "config",
    "ingest",
    "dem",
    "gslc",
    "gridgate",
    "qa",
    "util",
]
