"""
Dataclass-backed run configuration for Track G.

One YAML drives the whole run. Ergonomics copied from the user's ISCE2 gen-2
wrapper: a YAML supplies values, unknown keys WARN rather than fail, and
`--config` values can still be overridden on the command line.

Validation here is deliberately opinionated, because the two ways this workflow
silently produces garbage are both config-level:

  1. an unpinned geogrid (posting left blank -> ISCE3 falls back to the DEM's
     ~30 m spacing for a *complex* SLC, destroying resolution), and
  2. `flatten: false` (irreversible; the carrier phase stays in the product and
     every downstream interferogram is wrong).

Both are hard-checked below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_FREQUENCIES = ("A", "B")
VALID_POLS = ("HH", "HV", "VH", "VV")
VALID_DEM_SOURCES = ("NISAR", "COP", "NASA", "3DEP")
VALID_DATA_TYPES = ("complex32", "complex64", "complex64_zero_mantissa")
VALID_FS_STRATEGY = ("fsm", "page", "aggregate", "none")


class ConfigError(ValueError):
    """Raised for a configuration that cannot produce a correct product."""


# --------------------------------------------------------------------------
# leaf sections
# --------------------------------------------------------------------------
@dataclass
class Posting:
    """Output pixel spacing in metres (positive; sign handled by ISCE3)."""

    x: float
    y: float

    def validate(self, where: str) -> None:
        for name in ("x", "y"):
            v = getattr(self, name)
            if v is None:
                raise ConfigError(
                    f"{where}.{name} is null -- the geogrid would be UNPINNED. "
                    f"ISCE3 would then inherit the DEM spacing (~30 m) for a complex "
                    f"SLC, irreversibly decimating it. Set an explicit posting."
                )
            if float(v) <= 0:
                raise ConfigError(f"{where}.{name} must be > 0 (got {v})")


@dataclass
class RadarGridCube:
    """Geometry cube grid: coarse, and independent of the image posting."""

    posting: float = 1000.0
    heights: list[float] = field(
        default_factory=lambda: [-500.0, 0.0, 500.0, 1000.0, 1500.0, 2000.0, 3000.0]
    )

    def validate(self) -> None:
        if self.posting <= 0:
            raise ConfigError(f"geogrid.radar_grid_cube.posting must be > 0 (got {self.posting})")
        if len(self.heights) < 2:
            raise ConfigError("geogrid.radar_grid_cube.heights needs at least 2 levels")
        if sorted(self.heights) != list(self.heights):
            raise ConfigError("geogrid.radar_grid_cube.heights must be ascending")


@dataclass
class GeogridConfig:
    """
    The pinned output grid. Shared by BOTH dates -- that is the entire point.

    `epsg: null` means derive the UTM/polar-stereographic zone from the AOI
    centroid using nisar.workflows.dumpconfig.point_to_epsg.

    `snap` rounds the AOI corners outward to a multiple of this many metres.
    Default 1000 m is chosen because it divides evenly by every posting we
    realistically use (5, 8, 10, 20, 25, 40, 50 m), so freq-A and freq-B grids
    nest exactly and the corner coordinates stay human-readable.
    """

    epsg: int | None = None
    snap: float = 1000.0
    margin_m: float = 0.0
    #: Optional AOI as [west, south, east, north] in DEGREES. When set, the
    #: output grid covers this box INTERSECTED with the granule footprint,
    #: instead of the full footprint union.
    #:
    #: This is not a crop of the RSLC and never needs to be. gslc.py iterates
    #: geogrid blocks via isce3.core.rdr_geo_block_generator, which yields the
    #: radar-coordinate slice feeding each output block, and reads only
    #: `rslc_dataset[rdr_blk_slice]`. A smaller geogrid therefore reads less of
    #: the 24.5 GB granule automatically. Matches the course's own semantics in
    #: 2.1_ISCE3_TOPS_Processing/utils.py: "bbox_wsen=None -> stitch the ENTIRE
    #: union extent; bbox_wsen=wsen -> clip the output to the AOI wsen."
    aoi_lonlat: list[float] | None = None
    posting: dict[str, Posting] = field(
        default_factory=lambda: {
            "A": Posting(5.0, 5.0),
            "B": Posting(40.0, 5.0),
        }
    )
    radar_grid_cube: RadarGridCube = field(default_factory=RadarGridCube)

    def validate(self, frequencies: list[str]) -> list[str]:
        warnings: list[str] = []
        if self.epsg is not None and not (1024 <= int(self.epsg) <= 32767):
            raise ConfigError(f"geogrid.epsg {self.epsg} outside the 1024-32767 range ISCE3 accepts")
        if self.snap <= 0:
            raise ConfigError(f"geogrid.snap must be > 0 (got {self.snap})")
        if self.margin_m < 0:
            raise ConfigError(f"geogrid.margin_m must be >= 0 (got {self.margin_m})")
        if self.aoi_lonlat is not None:
            a = list(self.aoi_lonlat)
            if len(a) != 4:
                raise ConfigError(
                    f"geogrid.aoi_lonlat must be [west, south, east, north] "
                    f"(got {len(a)} value(s): {a})"
                )
            w, sth, e, n = (float(v) for v in a)
            if not (-180 <= w < e <= 180):
                raise ConfigError(
                    f"geogrid.aoi_lonlat west/east must satisfy -180 <= west < east <= 180 "
                    f"(got west={w}, east={e})"
                )
            if not (-90 <= sth < n <= 90):
                raise ConfigError(
                    f"geogrid.aoi_lonlat south/north must satisfy -90 <= south < north <= 90 "
                    f"(got south={sth}, north={n})"
                )
            self.aoi_lonlat = [w, sth, e, n]
        for freq in frequencies:
            if freq not in self.posting:
                raise ConfigError(
                    f"frequency {freq} selected but geogrid.posting.{freq} is missing"
                )
            self.posting[freq].validate(f"geogrid.posting.{freq}")
            # Nesting/readability check. Not fatal: the grid is pinned by
            # explicit absolute corners, so cross-date alignment holds either
            # way. But a non-dividing posting gives ragged corner coordinates
            # and breaks exact freq-A/freq-B nesting.
            for axis in ("x", "y"):
                step = float(getattr(self.posting[freq], axis))
                if abs(self.snap % step) > 1e-9 and abs(self.snap % step - step) > 1e-9:
                    warnings.append(
                        f"geogrid.snap ({self.snap}) is not an exact multiple of "
                        f"posting.{freq}.{axis} ({step}); grids will still be pinned and "
                        f"cross-date aligned, but freq A/B grids will not nest exactly"
                    )
        self.radar_grid_cube.validate()
        return warnings


@dataclass
class DemConfig:
    """
    DEM staging.

    ISCE3 geocoding requires ELLIPSOIDAL (WGS84) heights, not geoid/EGM.
    Both supported sardem routes deliver that:
      * NISAR : native ellipsoidal. Requires a NASA Earthdata account with an
                entry for urs.earthdata.nasa.gov in ~/.netrc (hard precondition,
                enforced by sardem before any network call).
      * COP   : EGM2008 delivered, converted to WGS84 by sardem unless
                --keep-egm. Ocean is nodata==0 BY DESIGN on this route
                (sardem passes -srcnodata 0 -dstnodata 0), so an ocean value of
                0 here is NOT evidence of a geoid-referenced DEM.
    """

    source: str = "NISAR"
    fallback: str | None = "COP"
    path: str | None = None
    buffer_deg: float = 0.1
    cache_dir: str | None = None

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.source not in VALID_DEM_SOURCES:
            raise ConfigError(
                f"dem.source '{self.source}' invalid; choose from {VALID_DEM_SOURCES}"
            )
        if self.fallback is not None and self.fallback not in VALID_DEM_SOURCES:
            raise ConfigError(
                f"dem.fallback '{self.fallback}' invalid; choose from {VALID_DEM_SOURCES} or null"
            )
        if not (0.0 <= self.buffer_deg <= 2.0):
            raise ConfigError(f"dem.buffer_deg {self.buffer_deg} outside a sane 0-2 degree range")
        if self.buffer_deg < 0.02:
            warnings.append(
                f"dem.buffer_deg {self.buffer_deg} is tight; the snapped UTM geogrid can "
                f"extend past the RSLC footprint and geocoding will fail on DEM edges"
            )
        return warnings


@dataclass
class Geo2Rdr:
    threshold: float = 1.0e-8
    maxiter: int = 25

    def validate(self) -> None:
        if self.threshold <= 0:
            raise ConfigError("gslc.geo2rdr.threshold must be > 0")
        if self.maxiter < 1:
            raise ConfigError("gslc.geo2rdr.maxiter must be >= 1")


@dataclass
class Blocksize:
    x: int = 1024
    y: int = 1024

    def validate(self) -> None:
        # bounds come straight from schemas/gslc.yaml
        if not (100 <= self.x <= 100000):
            raise ConfigError(f"gslc.blocksize.x must be in [100, 100000] (got {self.x})")
        if not (100 <= self.y <= 10000):
            raise ConfigError(f"gslc.blocksize.y must be in [100, 10000] (got {self.y})")


@dataclass
class GslcConfig:
    """Stage G1 knobs. Keys map 1:1 onto the installed gslc runconfig schema."""

    flatten: bool = True
    solid_earth_tides: bool = True
    data_type: str = "complex64_zero_mantissa"
    compression_enabled: bool = True
    compression_level: int = 1
    chunk_size: list[int] = field(default_factory=lambda: [512, 512])
    shuffle: bool = True
    fs_strategy: str = "page"
    fs_page_size: int = 4194304
    blocksize: Blocksize = field(default_factory=Blocksize)
    geo2rdr: Geo2Rdr = field(default_factory=Geo2Rdr)
    gpu_enabled: bool = False
    internet_access: bool = False
    debug_switch: bool = False
    # optional per-date ancillary files, keyed by YYYYMMDD
    orbit_files: dict[str, str] = field(default_factory=dict)
    tec_files: dict[str, str] = field(default_factory=dict)

    #: Restrict geocoding to these acquisition dates (YYYYMMDD). Empty -> every
    #: date in stack.json. Geocoding is per-date and independent, so a subset
    #: now can be topped up later without redoing anything. The PINNED geogrid
    #: is unaffected -- it lives in stack.json, fixed at ingest from the union
    #: of ALL footprints, so a subset lands on the same lattice and stays
    #: pixel-aligned with dates geocoded afterwards.
    dates: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not self.flatten:
            raise ConfigError(
                "gslc.flatten is false. Flattening removes the range carrier phase and is "
                "IRREVERSIBLE downstream: every interferogram formed from an unflattened "
                "GSLC pair carries a huge topographic/geometric ramp. Set flatten: true "
                "unless you are deliberately producing a non-interferometric product."
            )
        if self.data_type not in VALID_DATA_TYPES:
            raise ConfigError(
                f"gslc.data_type '{self.data_type}' invalid; choose from {VALID_DATA_TYPES}"
            )
        if self.fs_strategy not in VALID_FS_STRATEGY:
            raise ConfigError(
                f"gslc.fs_strategy '{self.fs_strategy}' invalid; choose from {VALID_FS_STRATEGY}"
            )
        if not (1 <= self.compression_level <= 9):
            raise ConfigError("gslc.compression_level must be in [1, 9]")
        if len(self.chunk_size) != 2 or any(c < 4 for c in self.chunk_size):
            raise ConfigError("gslc.chunk_size must be two integers >= 4")
        if self.fs_page_size < 1:
            raise ConfigError("gslc.fs_page_size must be >= 1")
        self.blocksize.validate()
        self.geo2rdr.validate()
        if self.gpu_enabled:
            warnings.append(
                "gslc.gpu_enabled is true; this machine has no CUDA device recorded. "
                "ISCE3 will raise at runtime if no GPU is present."
            )
        if not self.solid_earth_tides:
            warnings.append(
                "gslc.solid_earth_tides is false; geolocation will carry a few-cm "
                "tidal bias that differs between the two dates"
            )
        return warnings


@dataclass
class QaConfig:
    """Decimated-read QA. `max_pixels` is the target long edge of an overview."""

    enabled: bool = True
    max_pixels: int = 2000
    max_read_bytes: int = 256 * 1024 * 1024
    dpi: int = 110
    rslc_quicklook: bool = True

    def validate(self) -> None:
        if self.max_pixels < 64:
            raise ConfigError("qa.max_pixels must be >= 64")
        if self.max_read_bytes < 8 * 1024 * 1024:
            raise ConfigError("qa.max_read_bytes must be >= 8 MiB")


@dataclass
class WaterMaskConfig:
    """
    Water mask, replacing the course's broken NASADEM bit-15 route.

    `sea_level_margin_m` is applied to ORTHOMETRIC height, so the DEM is
    converted from its native WGS84 ellipsoidal datum via `geoid_crs` first.
    Thresholding our ellipsoidal DEM at 0 m the way the course does would mask
    every coastal pixel below ~30 m -- 1,591 km2 of real land on this scene.

    The margin is +1 m rather than 0 m because the DEM's ocean fill lands
    exactly on H = 0 with sub-metre scatter; at 0 m the test splits the ocean
    population in half (49.7% of deep water captured), while anywhere in
    0.25-3 m sits on a flat plateau at ~98.2%. See watermask.py.
    """

    enabled: bool = True
    method: str = "dem_orthometric"          # or "amplitude"
    reference_raster: str | None = None      # grid to build on; relative to out_root
    geoid_crs: str = "EPSG:9518"             # WGS84 + EGM2008 height
    sea_level_margin_m: float = 1.0
    amplitude_db: float | None = None        # 20*log10(magnitude), NOT 10*log10
    amplitude_percentile: float | None = None
    include_inland: bool = False
    inland_db: float = -14.0
    inland_min_elev_m: float = 5.0
    inland_min_area_px: int = 400
    ocean_probe: list[float] | None = None   # [x0, y0, x1, y1] in the grid CRS
    min_water_fraction: float = 0.001
    max_water_fraction: float = 0.999
    block_rows: int = 512

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.method not in ("dem_orthometric", "amplitude"):
            raise ConfigError(
                f"watermask.method '{self.method}' invalid; "
                "choose 'dem_orthometric' or 'amplitude'"
            )
        if self.method == "amplitude" and self.amplitude_db is None \
                and self.amplitude_percentile is None:
            raise ConfigError(
                "watermask.method 'amplitude' needs amplitude_db or amplitude_percentile"
            )
        if self.amplitude_db is not None and self.amplitude_db > -8.0:
            warnings.append(
                f"watermask.amplitude_db {self.amplitude_db} is suspiciously close to 0. "
                f"This field is TRUE dB = 20*log10(magnitude). A sea/land split on this "
                f"scene sits near -13 dB, so a value around -6.5 is the signature of a "
                f"10*log10 convention applied to a magnitude raster, which halves every "
                f"dB value -- double it. (The -6.48 dB figure quoted for this case is "
                f"exactly that: its true-dB equivalent is -12.96 dB.)"
            )
        if not (-5.0 <= self.sea_level_margin_m <= 50.0):
            raise ConfigError(
                f"watermask.sea_level_margin_m {self.sea_level_margin_m} outside a sane "
                f"-5..50 m range"
            )
        if self.sea_level_margin_m < 0.25:
            warnings.append(
                f"watermask.sea_level_margin_m {self.sea_level_margin_m} sits on the "
                f"ocean-fill ridge at H=0; measured capture of deep water there is only "
                f"~50%. Use 0.25-3 m."
            )
        if self.ocean_probe is not None and len(self.ocean_probe) != 4:
            raise ConfigError("watermask.ocean_probe must be [x0, y0, x1, y1]")
        return warnings


@dataclass
class IgramConfig:
    """
    Stage G3 -- interferogram, coherence, per-date amplitude.

    `looks_y` / `looks_x` are block-average factors in ROWS and COLUMNS of the
    GSLC grid, not in radar range/azimuth. On the freq-B 40 x 5 m posting,
    16 x 2 gives square 80 x 80 m output pixels.

    `block_rows` should stay a multiple of BOTH `looks_y` and the HDF5 chunk row
    size (512 for these products). A block that straddles a chunk boundary makes
    gzip re-inflate the same chunk twice.
    """

    enabled: bool = True
    freq: str | None = None                  # None -> frequencies[0]
    pol: str | None = None                   # None -> polarizations[0]
    #: Window (px) for the sliding-window coherence estimator, used when the
    #: multilook box has fewer than 4 samples. Phase stays per-pixel; only the
    #: coherence uses a neighbourhood, so the output grid is unchanged.
    coherence_window: int = 5
    looks_y: int = 16
    looks_x: int = 2
    block_rows: int = 1024
    per_date_amplitude: bool = True
    pairs: list[list[str]] = field(default_factory=list)
    pair_dir_template: str = "pairs/{ref}_{sec}/trackG"
    #: {ly}/{lx} are the looks. They are in the name because they CHANGE THE
    #: OUTPUT: a re-run at different looks writes a different grid, and without
    #: them in the identity it silently overwrites the previous product and
    #: passes any exists() check. This project has hit that failure mode six
    #: times; the looks belong in the filename.
    prefix_template: str = "ifg_{freq}_{pol}_{ly}x{lx}"

    def validate(self, frequencies: list[str], polarizations: list[str]) -> list[str]:
        warnings: list[str] = []
        if self.freq is not None and self.freq not in VALID_FREQUENCIES:
            raise ConfigError(f"igram.freq '{self.freq}' invalid; choose from {VALID_FREQUENCIES}")
        if self.freq is not None and self.freq not in frequencies:
            raise ConfigError(
                f"igram.freq '{self.freq}' is not in `frequencies` {frequencies}; "
                f"no GSLC would exist for it"
            )
        if self.pol is not None and self.pol not in VALID_POLS:
            raise ConfigError(f"igram.pol '{self.pol}' invalid; choose from {VALID_POLS}")
        if self.pol is not None and self.pol not in polarizations:
            raise ConfigError(
                f"igram.pol '{self.pol}' is not in `polarizations` {polarizations}; "
                f"geocode was never asked to produce it"
            )
        for name in ("looks_y", "looks_x"):
            v = int(getattr(self, name))
            if v < 1:
                raise ConfigError(f"igram.{name} must be >= 1 (got {v})")
        if self.block_rows < self.looks_y:
            raise ConfigError(
                f"igram.block_rows ({self.block_rows}) is smaller than looks_y "
                f"({self.looks_y}); no complete look box would fit in a block"
            )
        if self.block_rows % self.looks_y:
            warnings.append(
                f"igram.block_rows ({self.block_rows}) is not a multiple of looks_y "
                f"({self.looks_y}); it will be rounded down to "
                f"{(self.block_rows // self.looks_y) * self.looks_y} so look boxes stay "
                f"aligned to block boundaries"
            )
        if self.block_rows % 512:
            warnings.append(
                f"igram.block_rows ({self.block_rows}) is not a multiple of the 512-row "
                f"HDF5 chunking; every block will straddle a chunk boundary and gzip will "
                f"re-inflate the same chunk twice"
            )
        return warnings


@dataclass
class UnwrapConfig:
    """
    Stage G4 -- Goldstein filter, phase-sigma coherence, water mask, SNAPHU.

    THE nlooks ASYMMETRY. Two different quantities, fed differently, and this is
    where a port of the course silently goes wrong:

        phsig  gets the NOMINAL look count       nlks   = looks_y * looks_x
        snaphu gets the EFFECTIVE look count     nlooks = nominal / oversample^2

    In the course's 4 x 2 configuration those are 8 and 5.56 -- and the course's
    `generate_phsig_coh_tif` DEFAULT nlks is also 8, so its stack notebook can
    omit the argument and still be correct by coincidence. At 16 x 2 = 32 looks
    the coincidence breaks. `nlooks_nominal: null` derives the nominal count from
    `igram.looks_y * igram.looks_x` so the two cannot drift apart.

    `oversample_factor` 1.2 is the ISCE2 convention (1.2 per dimension, 1.44
    total) for turning nominal looks into independent looks. It is not derived
    for a geocoded GSLC geometry -- see unwrap.py -- but it errs conservative and
    it agrees with the measured coherence floor here.
    """

    enabled: bool = True
    filter_alpha: float = 0.5                # course default, never changed there
    filter_psize: int = 32                   # course default
    phsig_win: int = 5
    phsig_grad_win: int = 5
    phsig_batch: int = 5000                  # course 500; pure speed, no numerics
    nlooks_nominal: int | None = None        # None -> looks_y * looks_x
    oversample_factor: float = 1.2
    cost: str = "smooth"
    init: str = "mcf"
    ntiles: list[int] = field(default_factory=lambda: [1, 1])
    tile_overlap: int = 0
    nproc: int = 1
    single_tile_reoptimize: bool = True
    #: snaphu regrows conncomp labels "using a single tile" after tiled
    #: unwrapping. Same full-grid memory exposure as single_tile_reoptimize,
    #: though cheaper (labelling, not a network-flow re-solve).
    regrow_conncomps: bool = True
    water_mask: bool = True
    water_max_fraction: float = 0.60
    scratchdir: str | None = None

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not (0.0 <= self.filter_alpha <= 1.0):
            raise ConfigError(
                f"unwrap.filter_alpha {self.filter_alpha} outside [0, 1]; it is the "
                f"exponent in H = |S|**alpha"
            )
        if self.filter_psize < 4 or (self.filter_psize & (self.filter_psize - 1)):
            raise ConfigError(
                f"unwrap.filter_psize {self.filter_psize} must be a power of two >= 4 "
                f"(the filter takes an FFT of each patch)"
            )
        for name in ("phsig_win", "phsig_grad_win"):
            v = int(getattr(self, name))
            if v < 3:
                raise ConfigError(f"unwrap.{name} must be >= 3 (got {v})")
            if v % 2 == 0:
                warnings.append(
                    f"unwrap.{name} {v} is even; it will be incremented to {v + 1} "
                    f"(the window must be centred on a pixel)"
                )
        if self.phsig_batch < 1:
            raise ConfigError("unwrap.phsig_batch must be >= 1")
        if self.cost not in ("smooth", "defo", "topo", "p-norm"):
            raise ConfigError(
                f"unwrap.cost '{self.cost}' invalid; snaphu accepts "
                f"'smooth' / 'defo' / 'topo' / 'p-norm' (the course uses 'smooth')"
            )
        if self.init not in ("mcf", "mst"):
            raise ConfigError(f"unwrap.init '{self.init}' invalid; choose 'mcf' or 'mst'")
        if self.oversample_factor < 1.0:
            raise ConfigError(
                f"unwrap.oversample_factor {self.oversample_factor} is below 1.0, which "
                f"would claim MORE independent looks than samples averaged"
            )
        if len(self.ntiles) != 2 or any(int(t) < 1 for t in self.ntiles):
            raise ConfigError("unwrap.ntiles must be two integers >= 1")
        if list(self.ntiles) != [1, 1]:
            warnings.append(
                f"unwrap.ntiles {list(self.ntiles)} DIVERGES from the course, which "
                f"unwraps single-tile. Tiling is much faster and lighter, but per-tile "
                f"reoptimisation changes the solution and the connected-component "
                f"labelling -- it is a scientific change, not a performance flag."
            )
        if self.nproc < 1:
            raise ConfigError("unwrap.nproc must be >= 1")
        if not (0.0 < self.water_max_fraction <= 1.0):
            raise ConfigError("unwrap.water_max_fraction must be in (0, 1]")
        if self.water_max_fraction > 0.9:
            warnings.append(
                f"unwrap.water_max_fraction {self.water_max_fraction} is high enough to "
                f"admit a near-all-water mask, which is the exact signature of the broken "
                f"NASADEM producer (it pre-fills with WATER and exits 0 when tiles 404)"
            )
        return warnings


@dataclass
class OverlayConfig:
    """
    Stage G5 -- the folium HTML.

    `embed: true` inlines every PNG as a base64 data URI for a single portable
    file. That costs ~4/3 the PNG bytes AND is parsed on every page load
    regardless of which layers are switched on, so it is only reasonable
    together with `decimate: 2`.
    """

    enabled: bool = True
    opacity: float = 0.85
    decimate: int = 1
    embed: bool = False
    zoom_start: int = 9
    basemap_url: str = "https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
    basemap_attr: str = "Google Satellite"
    max_zoom: int = 20
    amplitude_percentiles: list[float] = field(default_factory=lambda: [2.0, 98.0])
    unwrap_percentiles: list[float] = field(default_factory=lambda: [2.0, 98.0])
    include_phsig: bool = True
    html_name: str = "trackG_overlay.html"

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not (0.0 < self.opacity <= 1.0):
            raise ConfigError(f"overlay.opacity must be in (0, 1] (got {self.opacity})")
        if self.decimate < 1:
            raise ConfigError(f"overlay.decimate must be >= 1 (got {self.decimate})")
        for name in ("amplitude_percentiles", "unwrap_percentiles"):
            v = list(getattr(self, name))
            if len(v) != 2 or not (0 <= v[0] < v[1] <= 100):
                raise ConfigError(f"overlay.{name} must be [lo, hi] with 0 <= lo < hi <= 100")
        if not self.html_name.endswith(".html"):
            raise ConfigError("overlay.html_name must end in .html")
        if self.embed and self.decimate == 1:
            warnings.append(
                "overlay.embed is true at full resolution: the HTML will carry ~50 MB of "
                "base64, parsed on every load whether or not a layer is visible. Set "
                "overlay.decimate: 2, or leave embed false and ship the _layers/ directory."
            )
        return warnings


# --------------------------------------------------------------------------
# Track R -- RSLC coregistration in radar coordinates (nisar.workflows.insar)
# --------------------------------------------------------------------------
@dataclass
class Looks:
    """
    Multilook factors in RADAR geometry, consumed at `crossmul`.

    `azimuth` multiplies the along-track spacing; `range` multiplies the SLANT
    range spacing, so the ground-range cell is `range * slantRangeSpacing /
    sin(incidence)` -- which is exactly `range * sceneCenterGroundRangeSpacing`.
    """

    azimuth: int = 1
    range: int = 1


@dataclass
class TrackRConfig:
    """
    Track R -- the conventional reference-scene chain, all in radar coordinates:

        rdr2geo -> geo2rdr -> coarse_resample -> dense_offsets -> rubbersheet
                -> fine_resample -> crossmul   [-> filter -> unwrap -> geocode]

    Two properties drive every sizing decision here:

    1. **The reference image is never resampled.** Only the secondary is, which
       is what Track R buys over Track G's geocode-to-a-pinned-grid.

    2. **Looks are consumed at `crossmul`, the LAST stage.** Every stage before
       it runs at full radar-grid resolution, so raising `looks` does not reduce
       coregistration cost at all -- neither RAM, nor scratch, nor runtime.
       `rdr2geo` alone writes x/y/z as Float64 over the whole reference grid.

    `product_type` decides which stages run (nisar/workflows/insar.py):
       RIFG -> D1-D8 + D14   (coregistration + crossmul; NO unwrap)
       RUNW -> + phase unwrap
       GUNW -> + geocode
    RIFG is the right target on a memory-bounded box: it is the whole
    coregistration chain, and it stops before snaphu.
    """

    enabled: bool = True

    #: Frequency to coregister. One only -- the chain is per-frequency and the
    #: scratch cost of the other band is not shared.
    frequency: str = "B"
    #: Polarization for crossmul. HH is the only co-pol in these DHDH granules.
    polarization: str = "HH"

    #: RIFG | RUNW | GUNW | RIFG_RUNW_GUNW. See the class docstring.
    product_type: str = "RIFG"

    #: Per-frequency multilook, applied at crossmul. Keyed by frequency so a
    #: single config can carry the right looks for both bands: the two have the
    #: same azimuth spacing but an 8x different range spacing, so one shared
    #: (azimuth, range) pair cannot be correct for both.
    looks: dict[str, Looks] = field(
        default_factory=lambda: {"A": Looks(5, 5), "B": Looks(9, 1)}
    )

    #: Empty -> every consecutive date pair, matching igram.pairs semantics.
    pairs: list[list[str]] = field(default_factory=list)

    #: Refuse to start unless this much disk is free. rdr2geo + geo2rdr +
    #: two resampled SLCs is ~21 GB per pair on freq B and ~170 GB on freq A.
    min_free_gb: float = 60.0

    #: Hard stop on a frequency whose scratch cost cannot fit. Set false only
    #: if you have deliberately checked the disk.
    enforce_disk_gate: bool = True

    # -- stage knobs, mapped 1:1 onto the installed insar schema -------------
    rdr2geo_threshold: float = 1.0e-7
    rdr2geo_numiter: int = 25
    rdr2geo_extraiter: int = 10
    rdr2geo_lines_per_block: int = 1000

    geo2rdr_threshold: float = 1.0e-8
    geo2rdr_maxiter: int = 25
    geo2rdr_lines_per_block: int = 1000

    #: Memory budget per streaming block, in MB. Block heights for rdr2geo,
    #: geo2rdr, dense_offsets and crossmul are DERIVED from this and the
    #: frequency's range width, because a fixed `lines_per_block` costs 8x more
    #: on freq A (54244 samples) than freq B (6781) and OOMs a small box.
    block_budget_mb: float = 256.0

    #: dense_offsets is ampcor -- the data-driven refinement Track G has no
    #: equivalent for. Disabling it forces rubbersheet AND fine_resample off,
    #: and crossmul then falls back to the coarse-resampled secondary.
    dense_offsets_enabled: bool = True
    dense_offsets_lines_per_block: int = 1000
    window_range: int = 64
    window_azimuth: int = 64
    half_search_range: int = 20
    half_search_azimuth: int = 20
    skip_range: int = 32
    skip_azimuth: int = 32

    coarse_lines_per_tile: int = 1000
    coarse_columns_per_tile: int = 1000
    fine_lines_per_tile: int = 100
    fine_columns_per_tile: int = 0

    crossmul_flatten: bool = True
    crossmul_oversample: int = 2
    crossmul_lines_per_block: int = 1024
    common_band_range_filter: bool = False
    common_band_azimuth_filter: bool = False

    gpu_enabled: bool = False

    # -- phase unwrapping (needed ONLY to reach the ionosphere stage) --------
    #
    # These looks are INDEPENDENT of `looks` above. unwrap.py:122-135 re-runs
    # crossmul from the coregistered SLCs at these factors, so the RUNW grid
    # gets a FRESHLY COMPUTED coherence -- it does not decimate the RIFG's.
    # That is what lets the RIFG stay at 1x1 (where coherence is degenerately
    # 1.0) while the unwrapped product still has a real coherence to unwrap on.
    #
    # It is also a hard memory constraint: unwrap.py:278-279 open_raster()s the
    # WHOLE interferogram and coherence. At 1x1 on freq A that is 23.1 + 11.5 =
    # 34.6 GB, over this box's 31 GB. At 4x4 it is ~2.2 GB.
    phase_unwrap_range_looks: int = 4
    phase_unwrap_azimuth_looks: int = 4
    unwrap_algorithm: str = "snaphu"
    #: snaphu peak RAM is per-TILE x nproc, measured at 385 bytes/pixel.
    #: On the 13300 x 13561 RUNW grid: [4,4] gives 13.06 Mpx tiles = 5.03 GB per
    #: process, 40.2 GB across 8 -- an OOM. [8,8] gives 3.74 Mpx = 1.44 GB each,
    #: 11.5 GB total.
    unwrap_ntiles: list[int] = field(default_factory=lambda: [8, 8])
    unwrap_tile_overlap: list[int] = field(default_factory=lambda: [128, 128])
    unwrap_nproc: int = 8
    #: phase_unwrap.bridge stitches disconnected components AFTER snaphu.
    #: unwrap.py:356 does `dst_h5[unw_path][()]` -- a WHOLE-array read of the
    #: unwrapped phase -- then bridge_unwrapped_phase adds a bool mask and an
    #: int32 label array. On a 1x1 frequency A grid (2886 Mpx) that is
    #: 11.5 + 2.9 + 11.5 = ~26 GB minimum, and it happens AFTER snaphu has
    #: already run for hours. Turn it off for full-resolution unwraps.
    unwrap_bridge_enabled: bool = True

    #: Effective looks handed to snaphu. Leave null and ISCE3 derives it at
    #: unwrap.py:563 as `rg_spac * az_spac / (rg_res * az_res)` -- reading the
    #: spacings from the RIFG's OWN interferogram group. That grid is set by
    #: `crossmul` looks, NOT by phase_unwrap looks, so at crossmul 1x1 it
    #: computes the effective looks of the 1x1 grid (0.619 here, because the
    #: SLC is oversampled relative to its resolution cell) and then hands that
    #: to snaphu, which rejects anything < 1:
    #:     ValueError: nlooks must be >= 1, instead got 0.6189996726516942
    #: The array actually being unwrapped is the 4x4 re-multilook, whose
    #: effective looks is 16x that = 9.904. Set it explicitly whenever
    #: crossmul looks and phase_unwrap looks differ.
    unwrap_nlooks: float | None = None

    #: Path to an existing RIFG, for running `python -m nisar.workflows.unwrap`
    #: standalone against a completed coregistration instead of repeating it.
    unwrap_crossmul_path: str | None = None

    #: MUST stay false whenever ntiles != [1,1]. snaphu-py's post-processing
    #: "re-optimize using a SINGLE tile" pass undoes the entire memory benefit
    #: of tiling: 180.4 Mpx x 385 B = 69 GB, well over this box.
    #: Cost of false: connected-component labels come from the tiled solution
    #: and its stitching, so seams matter more.
    unwrap_single_tile_reoptimize: bool = False
    #: Also false -- regrow_conncomps relabels "using a single tile" too, so it
    #: carries the same unbounded full-grid cost.
    unwrap_regrow_conncomps: bool = False

    # -- ionosphere: split-spectrum -----------------------------------------
    #
    # GATED ON RUNW. insar.py:120-124 requires 'RUNW' in out_paths, so with
    # product_type RIFG this stage is skipped SILENTLY -- no error, no output.
    #
    # main_side_band uses frequencies A and B directly. It is the right method
    # for NISAR DHDH data: A and B share a starting range and their range
    # spacings are in an exact 8:1 ratio, so decimating A onto B is exact.
    # split_main_band instead splits A's own 40 MHz into sub-bands, which costs
    # two extra full-resolution unwraps and ~92 GB of sub-band SLCs, and is
    # noisier here.
    #
    # Only frequency A goes in `list_of_frequencies`: ionosphere.py builds the
    # frequency B pair itself by decimating frequency A's geo2rdr/rubbersheet
    # offsets, so a second Track R run on freq B is NOT required.
    ionosphere_enabled: bool = False
    ionosphere_spectral_diversity: str = "main_side_band"
    ionosphere_lines_per_block: int = 1000
    #: Dispersive/non-dispersive separation amplifies unwrapped-phase noise by
    #: ~17x for this A/B pair (40 MHz vs 5 MHz, only 54.5 MHz apart). Filtering
    #: is not optional here -- unfiltered, the estimate is worse than none.
    ionosphere_filter_enabled: bool = True
    ionosphere_filter_coherence_threshold: float = 0.5
    ionosphere_median_filter_size: int = 15

    pair_dir_template: str = "pairs/{ref}_{sec}/trackR"

    def looks_for(self, freq: str) -> Looks:
        """Looks for one frequency, defaulting to 1x1 rather than guessing."""
        lk = self.looks.get(freq)
        if lk is None:
            raise ConfigError(
                f"track_r.looks has no entry for frequency '{freq}'. "
                f"Present: {sorted(self.looks)}. Add one -- the correct looks "
                f"differ by ~8x in range between freq A and freq B."
            )
        return lk

    def validate(self) -> list[str]:
        warnings: list[str] = []

        if self.frequency not in VALID_FREQUENCIES:
            raise ConfigError(
                f"track_r.frequency '{self.frequency}' invalid; "
                f"choose one of {VALID_FREQUENCIES}"
            )
        if self.polarization not in VALID_POLS:
            raise ConfigError(
                f"track_r.polarization '{self.polarization}' invalid; "
                f"choose from {VALID_POLS}"
            )

        valid_types = ("RIFG", "RUNW", "GUNW", "RIFG_RUNW_GUNW", "ROFF", "GOFF", "ROFF_GOFF")
        if self.product_type not in valid_types:
            raise ConfigError(
                f"track_r.product_type '{self.product_type}' invalid; "
                f"choose from {list(valid_types)}"
            )

        for freq, lk in self.looks.items():
            if freq not in VALID_FREQUENCIES:
                raise ConfigError(
                    f"track_r.looks has an entry for '{freq}', which is not a "
                    f"valid frequency {VALID_FREQUENCIES}"
                )
            if int(lk.azimuth) < 1 or int(lk.range) < 1:
                raise ConfigError(
                    f"track_r.looks.{freq} must have azimuth >= 1 and range >= 1 "
                    f"(got azimuth={lk.azimuth}, range={lk.range})"
                )

        # The knob exists, so guard the failure mode the plan documents.
        if self.product_type in ("RUNW", "GUNW", "RIFG_RUNW_GUNW"):
            warnings.append(
                f"track_r.product_type '{self.product_type}' runs phase unwrapping. "
                f"On a memory-bounded box use RIFG: it is the whole coregistration "
                f"chain and stops before snaphu."
            )

        if self.ionosphere_enabled and self.product_type not in (
                "RUNW", "GUNW", "RIFG_RUNW_GUNW"):
            raise ConfigError(
                f"track_r.ionosphere_enabled is true but product_type is "
                f"'{self.product_type}'. insar.py:120-124 gates the ionosphere "
                f"stage on 'RUNW' in out_paths, so it would be SKIPPED SILENTLY "
                f"-- no error and no output. Set product_type: RUNW."
            )

        valid_sd = ("split_main_band", "main_side_band", "main_diff_ms_band",
                    "main_diff_low_high_subband")
        if self.ionosphere_spectral_diversity not in valid_sd:
            raise ConfigError(
                f"track_r.ionosphere_spectral_diversity "
                f"'{self.ionosphere_spectral_diversity}' invalid; "
                f"choose from {list(valid_sd)}"
            )

        for name in ("phase_unwrap_range_looks", "phase_unwrap_azimuth_looks"):
            if int(getattr(self, name)) < 1:
                raise ConfigError(f"track_r.{name} must be >= 1")

        if not self.dense_offsets_enabled:
            warnings.append(
                "track_r.dense_offsets_enabled is false, which forces rubbersheet "
                "AND fine_resample off; crossmul then uses the COARSE-resampled "
                "secondary, i.e. geometry-only coregistration with no data-driven "
                "refinement"
            )

        if self.crossmul_oversample < 2:
            warnings.append(
                f"track_r.crossmul_oversample is {self.crossmul_oversample}; "
                f"crossmul aliases the conjugate product below 2"
            )

        if self.gpu_enabled:
            warnings.append("track_r.gpu_enabled is true; there is no CUDA device here")

        return warnings


@dataclass
class StepToggles:
    """Per-stage on/off. The CLI's --only/--start-step/--stop-step layer on top."""

    ingest: bool = True
    dem: bool = True
    gslc: bool = True
    gridgate: bool = True
    qa: bool = True
    igram: bool = True
    watermask: bool = True
    unwrap: bool = True
    overlay: bool = True


# --------------------------------------------------------------------------
# root
# --------------------------------------------------------------------------
@dataclass
class Config:
    case_name: str
    case_dir: str
    out_root: str | None = None
    granules: list[str] = field(default_factory=list)
    frequencies: list[str] = field(default_factory=lambda: ["B"])
    polarizations: list[str] = field(default_factory=lambda: ["HH"])
    geogrid: GeogridConfig = field(default_factory=GeogridConfig)
    dem: DemConfig = field(default_factory=DemConfig)
    gslc: GslcConfig = field(default_factory=GslcConfig)
    qa: QaConfig = field(default_factory=QaConfig)
    igram: IgramConfig = field(default_factory=IgramConfig)
    watermask: WaterMaskConfig = field(default_factory=WaterMaskConfig)
    unwrap: UnwrapConfig = field(default_factory=UnwrapConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    track_r: TrackRConfig = field(default_factory=TrackRConfig)
    steps: StepToggles = field(default_factory=StepToggles)

    # populated by from_yaml
    config_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    # ---------------- derived paths ----------------
    @property
    def root(self) -> Path:
        return Path(self.out_root) if self.out_root else Path(self.case_dir)

    @property
    def case(self) -> Path:
        return Path(self.case_dir)

    @property
    def cfg_dir(self) -> Path:
        return self.root / "cfg"

    @property
    def prov_dir(self) -> Path:
        return self.root / "provenance"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def gslc_dir(self) -> Path:
        return self.root / "L2_GSLC"

    @property
    def scratch_dir(self) -> Path:
        return self.root / "scratch"

    @property
    def qa_dir(self) -> Path:
        return self.root / "qa"

    @property
    def aux_dir(self) -> Path:
        return self.root / "aux"

    @property
    def stack_json(self) -> Path:
        return self.root / "stack.json"

    @property
    def time_summary(self) -> Path:
        return self.root / "time_summary.txt"

    @property
    def dem_path(self) -> Path:
        if self.dem.path:
            return Path(self.dem.path)
        return self.aux_dir / "dem" / f"dem_{self.case_name}.tif"

    def gslc_output(self, date: str, freq_tag: str) -> Path:
        """One GSLC per date per selected-frequency-set."""
        return self.gslc_dir / f"{date}_gslc_freq{freq_tag}.h5"

    def resolve_gslc(self, date: str, freq: str | None = None) -> Path:
        """
        The GSLC file on disk that holds `freq` for `date`.

        `gslc_output` is keyed by freq_tag = the joined selected-frequency SET,
        because stage G1 writes ONE combined product per date when several bands
        are geocoded in a single run. But when the bands are run separately --
        which a memory-bounded box forces, and which both this case and the AOI
        case actually did -- each date has its own `..._gslc_freqA.h5` and
        `..._gslc_freqB.h5`, and NO `..._gslc_freqAB.h5` ever exists.

        Every stage that consumes GSLCs must therefore go through here rather
        than calling `gslc_output(date, cfg.freq_tag)` directly. Fixing only the
        interferogram call site left `gridgate` demanding a `freqAB.h5` that
        could not exist, which failed a run AFTER all four products were built.

        Prefers the single-band file, falls back to the combined one, and when
        neither is present reports the single-band name, since that is what a
        per-band run produces and therefore the actionable one.
        """
        if freq is not None:
            single = self.gslc_output(date, freq)
            if single.exists():
                return single
        combined = self.gslc_output(date, self.freq_tag)
        if combined.exists():
            return combined
        for f in self.frequencies:
            cand = self.gslc_output(date, f)
            if cand.exists():
                return cand
        return self.gslc_output(date, freq or self.freq_tag)

    @property
    def igram_freq(self) -> str:
        """Frequency the interferometric stages work on. Defaults to the first selected."""
        return self.igram.freq or self.frequencies[0]

    @property
    def igram_pol(self) -> str:
        """
        Polarization the interferometric stages work on.

        Defaults to the first selected. For this data set that is HH, and HH is
        all there is: the granules are DHDH (HH + HV) and the L2 GSLCs carry HH
        only. THERE IS NO VV -- where a VV product is asked for, HH is the
        correct co-pol substitute and must be labelled as HH, not as VV.
        """
        return self.igram.pol or self.polarizations[0]

    @property
    def freq_tag(self) -> str:
        return "".join(sorted(self.frequencies))

    def selected_dates(self, stack: dict) -> list[str]:
        """
        Stack dates restricted by `gslc.dates`, in stack order.

        Every stage that enumerates dates MUST go through this. Filtering only
        in the gslc stage is how a partial run produces GSLCs for two dates and
        then fails in gridgate demanding a third that was never asked for.
        """
        dates = list(stack["dates"])
        if not self.gslc.dates:
            return dates
        want = {str(d) for d in self.gslc.dates}
        unknown = want - set(dates)
        if unknown:
            raise ConfigError(
                f"gslc.dates names {sorted(unknown)}, not in the stack {dates}"
            )
        return [d for d in dates if d in want]

    def mkdirs(self) -> None:
        for d in (
            self.cfg_dir,
            self.prov_dir,
            self.log_dir,
            self.gslc_dir,
            self.scratch_dir,
            self.qa_dir,
            self.dem_path.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ---------------- validation ----------------
    def validate(self) -> list[str]:
        # seed with warnings already collected during loading (unknown keys),
        # so they survive rather than being overwritten here
        warnings: list[str] = list(self.warnings)

        if not self.case_name or not str(self.case_name).strip():
            raise ConfigError("case_name is required")
        if not self.case_dir:
            raise ConfigError("case_dir is required")
        if not self.case.is_dir():
            raise ConfigError(
                f"case_dir does not exist: {self.case_dir}\n"
                f"  This must be the directory holding the NISAR L1 RSLC .h5 granules."
            )

        if not self.frequencies:
            raise ConfigError("frequencies must list at least one of 'A' / 'B'")
        bad = [f for f in self.frequencies if f not in VALID_FREQUENCIES]
        if bad:
            raise ConfigError(f"invalid frequencies {bad}; valid values are {VALID_FREQUENCIES}")
        if len(set(self.frequencies)) != len(self.frequencies):
            raise ConfigError(f"duplicate entries in frequencies: {self.frequencies}")

        if not self.polarizations:
            raise ConfigError("polarizations must list at least one of HH/HV/VH/VV")
        badp = [p for p in self.polarizations if p not in VALID_POLS]
        if badp:
            raise ConfigError(f"invalid polarizations {badp}; valid values are {VALID_POLS}")

        for g in self.granules:
            if not Path(g).is_absolute():
                if not (self.case / g).exists():
                    raise ConfigError(f"granule not found: {g} (relative to {self.case_dir})")
            elif not Path(g).exists():
                raise ConfigError(f"granule not found: {g}")

        warnings += self.geogrid.validate(self.frequencies)
        warnings += self.dem.validate()
        warnings += self.gslc.validate()
        self.qa.validate()
        warnings += self.igram.validate(self.frequencies, self.polarizations)
        warnings += self.watermask.validate()
        warnings += self.unwrap.validate()
        warnings += self.overlay.validate()
        warnings += self.track_r.validate()

        # cheap disk sanity: a GSLC is big and running out of space mid-geocode
        # wastes hours
        try:
            from .util import free_disk_bytes, human_bytes

            free = free_disk_bytes(self.case_dir)
            if free < 10 * 1024**3:
                warnings.append(
                    f"only {human_bytes(free)} free on the case_dir filesystem; "
                    f"GSLC output plus scratch commonly needs tens of GiB"
                )
        except Exception:
            pass

        self.warnings = warnings
        return warnings

    # ---------------- loading ----------------
    @classmethod
    def from_yaml(cls, path: str | os.PathLike, overrides: dict | None = None) -> "Config":
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")

        # CLI overrides win over the YAML, matching the ISCE2 gen-2 pattern
        if overrides:
            raw = _deep_merge(raw, {k: v for k, v in overrides.items() if v is not None})

        cfg, unknown = _build(cls, raw, prefix="")
        cfg.config_path = str(path.resolve())
        if unknown:
            cfg.warnings.append(
                "ignoring unknown config key(s): " + ", ".join(sorted(unknown))
            )
        return cfg

    def to_dict(self) -> dict:
        return _asdict(self)


# --------------------------------------------------------------------------
# generic dataclass <-> dict plumbing
# --------------------------------------------------------------------------
def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _build(klass, data: dict, prefix: str) -> tuple[Any, list[str]]:
    """
    Instantiate a (possibly nested) dataclass from a plain dict.

    Unknown keys are collected and reported rather than raising, so a config
    written for a newer version of the workflow still runs.
    """
    unknown: list[str] = []
    known = {f.name: f for f in fields(klass)}
    kwargs: dict[str, Any] = {}

    for key, value in data.items():
        if key not in known:
            unknown.append(f"{prefix}{key}")
            continue
        f = known[key]
        kwargs[key] = _coerce(f.type, value, f"{prefix}{key}.", unknown)

    try:
        obj = klass(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"could not build {klass.__name__} from config: {exc}") from exc
    return obj, unknown


# dataclasses whose dict form we recurse into
_NESTED = {
    "GeogridConfig": GeogridConfig,
    "DemConfig": DemConfig,
    "GslcConfig": GslcConfig,
    "QaConfig": QaConfig,
    "IgramConfig": IgramConfig,
    "WaterMaskConfig": WaterMaskConfig,
    "UnwrapConfig": UnwrapConfig,
    "OverlayConfig": OverlayConfig,
    "TrackRConfig": TrackRConfig,
    "Looks": Looks,
    "StepToggles": StepToggles,
    "RadarGridCube": RadarGridCube,
    "Geo2Rdr": Geo2Rdr,
    "Blocksize": Blocksize,
    "Posting": Posting,
}


def _coerce(type_hint: Any, value: Any, prefix: str, unknown: list[str]) -> Any:
    """Map a YAML value onto a field, recursing into nested dataclasses."""
    hint = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__name__", str(type_hint))

    # dict[str, Looks] -- the per-frequency radar-geometry multilook table
    if "dict[str, Looks]" in hint:
        if not isinstance(value, dict):
            raise ConfigError(
                f"{prefix.rstrip('.')} must be a mapping of frequency -> "
                f"{{azimuth, range}}"
            )
        looks_out: dict[str, Looks] = {}
        for freq, sub in value.items():
            if isinstance(sub, dict):
                obj, unk = _build(Looks, sub, f"{prefix}{freq}.")
                unknown.extend(unk)
                looks_out[str(freq)] = obj
            elif isinstance(sub, int):
                looks_out[str(freq)] = Looks(int(sub), int(sub))  # scalar -> square
            else:
                raise ConfigError(
                    f"{prefix}{freq} must be a mapping with azimuth/range, "
                    f"or a single integer"
                )
        return looks_out

    # dict[str, Posting] -- the per-frequency posting table
    if "dict[str, Posting]" in hint:
        if not isinstance(value, dict):
            raise ConfigError(f"{prefix.rstrip('.')} must be a mapping of frequency -> {{x, y}}")
        out: dict[str, Posting] = {}
        for freq, sub in value.items():
            if isinstance(sub, dict):
                obj, unk = _build(Posting, sub, f"{prefix}{freq}.")
                unknown.extend(unk)
                out[str(freq)] = obj
            elif isinstance(sub, (int, float)):
                out[str(freq)] = Posting(float(sub), float(sub))  # scalar -> isotropic
            else:
                raise ConfigError(
                    f"{prefix}{freq} must be a mapping with x/y, or a single number"
                )
        return out

    for name, klass in _NESTED.items():
        if hint.startswith(name) or hint == name:
            if value is None:
                return klass()
            if not isinstance(value, dict):
                raise ConfigError(f"{prefix.rstrip('.')} must be a mapping")
            obj, unk = _build(klass, value, prefix)
            unknown.extend(unk)
            return obj

    return value


def _asdict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _asdict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj
