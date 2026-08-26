"""
nisar_wf -- modular NISAR L1 RSLC -> L2 GSLC workflow package (Track G).

Stages implemented here
-----------------------
  A   ingest    : read RSLC metadata, write stack.json + PINNED geogrid
  B   dem       : stage an ellipsoidal-height DEM covering the AOI
  G1  gslc      : render + validate a gslc runconfig per date, run nisar.workflows.gslc
  G2  gridgate  : hard assert the two GSLCs are pixel-aligned
  QA  qa        : decimated-read quicklooks (never loads a full raster)
  G3  igram     : conjugate product + coherence + per-date amplitude, one pass
  W   watermask : water mask from the DEM in ORTHOMETRIC height
  G4  unwrap    : Goldstein -> phase-sigma coherence -> water mask -> SNAPHU
  G5  overlay   : folium HTML, every raster a layer over satellite tiles

Stages deliberately NOT implemented yet (see README "Slotting in the rest"):
      dolphin   : phase linking / time series over an N-date stack

A note that belongs at the top of this package: THIS PRODUCT HAS NO VV. The
granules are DHDH (HH + HV) and the L2 GSLCs carry HH only. Where a VV amplitude
is called for, HH is the correct co-pol substitute -- and it is labelled HH
everywhere, never VV.

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
    "igram",
    "watermask",
    "unwrap",
    "overlay",
    "util",
]
