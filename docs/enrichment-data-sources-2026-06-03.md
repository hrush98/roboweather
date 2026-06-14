# Data Sources: Where to Get Enriched Features for Training and Live Inference

**Date:** 2026-06-03
**Context:** Practical guide to sourcing every enrichment feature mentioned in the systematic edge analysis, covering both historical backfill and real-time availability.

---

## Quick Summary

The good news: roughly 40% of the recommended enrichment features are **already being fetched** by the IEM ASOS client but are **thrown away** by the feature builder. Another 20% come from the HRRR V2 cache framework that already exists but isn't wired into the research loop. The remaining 40% need new data sources integrated.

---

## 1. Already Fetched, Currently Unused (Zero New Dependencies)

### What we have

The `IEMASOSClient.DEFAULT_FIELDS` list already requests:
```
tmpf, dwpf, sknt, drct, relh, mslp, skyc1, skyc2, skyc3
```

But `build_same_day_features.py` only uses: `tmpf`, `dwpf`, `sknt`, `drct`, `skyc1`, `skyc2`, `skyc3`.

**Wasted fields:**
| IEM field | Meaning | Feature name | Why it matters |
|-----------|---------|-------------|----------------|
| `relh` | Relative humidity % | `relative_humidity` | Combined with temp gives wet-bulb; high RH suppresses diurnal range |
| `mslp` | Mean sea level pressure (mb) | `pressure_mslp` | Rising pressure = clearing/warming air mass; falling = approaching front |

**Can also add to DEFAULT_FIELDS (same API, just add the field name):**
| IEM field | Meaning | IEM name | Why it matters |
|-----------|---------|----------|----------------|
| `p01i` | 1-hour precip (inches) | `p01i` | Recent rain = evaporative cooling, suppresses afternoon high |
| `vsby` | Visibility (miles) | `vsby` | Low visibility = fog/stratus = capped heating |
| `skyl1` | Lowest cloud layer base (ft) | `skyl1` | Low ceiling <3000 ft strongly suppresses diurnal heating |
| `alti` | Altimeter setting (inches Hg) | `alti` | Station-pressure proxy, less smoothed than MSLP |
| `feel` | Apparent temperature (F) | `feel` | Wind chill / heat index — human-perceived temp, market psychology proxy |

### How to enable (training)

In `weather_trader/stations/iem_asos_client.py`, line 12-23, add the new fields to `DEFAULT_FIELDS`:
```python
DEFAULT_FIELDS = [
    "tmpf", "dwpf", "sknt", "drct", "relh", "mslp",
    "p01i", "vsby", "skyl1", "alti", "feel",   # NEW
    "skyc1", "skyc2", "skyc3",
]
```

Then in `weather_trader/features/build_same_day_features.py`:

1. In `prepare_station_observations()`, create derived features:
```python
# Already have relh and mslp in the frame — just pass them through
frame["relative_humidity"] = frame["relh"]  # rename for clarity
frame["pressure_mslp"] = frame["mslp"]

# New derived feature: wet bulb temperature (approximation)
# Tw = T * atan(0.151977 * (rh + 8.313659)^0.5) + atan(T + rh) - atan(rh - 1.676331) + 0.00391838 * rh^(3/2) * atan(0.023101 * rh) - 4.686035
# Simpler approximation adequate for same-day: Tw ≈ T - (T - Td) * 0.3 for 30-70% RH
frame["wet_bulb_approx"] = frame["tmpf"] - (frame["tmpf"] - frame["dwpf"]) * 0.35  # crude but useful

# Pressure tendency (requires second fetch — see section 2 for 3h-ago method)
# For a simpler version that doesn't need a second fetch:
frame["pressure_mslp"] = pd.to_numeric(frame["mslp"], errors="coerce")

# Visibility
frame["visibility_miles"] = pd.to_numeric(frame["vsby"], errors="coerce")

# Precipitation
frame["precip_1h_in"] = pd.to_numeric(frame["p01i"], errors="coerce")

# Cloud base (parse lowest layer — skyl1 is a string like "025" = 2500 ft)
# Actually skyl1 contains the sky cover code, not the height.
# For cloud base we need skyl1 from IEM but the field name is confusing.
# Better: query IEM with data=skyl1 which returns "sky_level_1_coverage|sky_level_1_altitude"
# We can use a custom parser for this.

# Altimeter
frame["altimeter"] = pd.to_numeric(frame["alti"], errors="coerce")

# Feels-like
frame["feels_like"] = pd.to_numeric(frame["feel"], errors="coerce")
```

2. Add the new features to the feature row dictionary in `_make_feature_row()`:
```python
"relative_humidity": snapshot.get("relative_humidity", np.nan),
"wet_bulb_approx": snapshot.get("wet_bulb_approx", np.nan),
"pressure_mslp": snapshot.get("pressure_mslp", np.nan),
"visibility_miles": snapshot.get("visibility_miles", np.nan),
"precip_1h_in": snapshot.get("precip_1h_in", np.nan),
"altimeter": snapshot.get("altimeter", np.nan),
```

3. Add to `BASE_FEATURE_COLUMNS` in `weather_trader/models/bucket_classifier.py`:
```python
"relative_humidity",
"wet_bulb_approx", 
"pressure_mslp",
"visibility_miles",
"precip_1h_in",
"altimeter",
```

4. For live inference, add to `StationWeatherState` in `weather_trader/execution/weather.py`:
```python
relative_humidity: float
pressure_mslp: float
wet_bulb_approx: float
visibility_miles: float
precip_1h_in: float
```

Then populate them in `WeatherFeatureService.get_state()` from the `latest` row, and pass them through `FairValueEngine` into the model feature vector.

### Cost and latency impact
- **Cost:** Free. IEM ASOS API is public and unauthenticated.
- **Latency:** Zero additional HTTP calls — these fields come in the same CSV response.
- **Rate limits:** IEM has a soft throttle (~1 req/sec). Adding fields doesn't change request count.
- **Historical backfill:** Works immediately — IEM has these fields back to at least 2000 for US ASOS stations.

---

## 2. Computable from Existing Data (No New API Calls)

### Features we can compute without any new data source:

| Feature | How to compute | Training | Live |
|---------|---------------|----------|------|
| `temp_range_so_far` | `max_temp_so_far - min_temp_so_far` | Already have both — just subtract | Same |
| `hour_since_sunrise` | `hour_local - sunrise_hour` (precompute sunrise table per station/DOY) | Need sunrise table | Same table lookup |
| `solar_noon_proximity` | `abs(hour_local * 60 + minute_local - solar_noon_minutes)` | Need solar noon table | Same |
| `wet_bulb_temp` | Stull approximation from T + RH (see above) | Already have T + dewpoint; also have RH | Same |
| `pressure_tendency_3h` | Need observation from ~3 hours ago — already in the same daily fetch | Already possible with `temp_change_3h` approach | Same |
| `yesterday_departure` | Yesterday's high - climo_high for that station/DOY | Need climo table (next section) | Precompute |

### Sunrise/solar noon table

This is pure math — no API needed. For any station lat/lon and date:

```python
import math
from datetime import date, datetime, timedelta

def solar_noon_utc(lat: float, lon: float, d: date) -> datetime:
    """Approximate solar noon to within ~1 minute using NOAA formula."""
    # Day of year
    doy = d.timetuple().tm_yday
    # Equation of time (minutes)
    b = (360 / 365) * (doy - 81)
    b_rad = math.radians(b)
    eot = 9.87 * math.sin(2 * b_rad) - 7.53 * math.cos(b_rad) - 1.5 * math.sin(b_rad)
    # Solar noon in UTC (approximate)
    lon_correction = lon / 15 * 60  # minutes
    noon_utc_minutes = 12 * 60 - eot - lon_correction
    hour = int(noon_utc_minutes // 60)
    minute = int(noon_utc_minutes % 60)
    return datetime(d.year, d.month, d.day, hour % 24, minute)
```

Store a precomputed CSV: `data/station_solar_times.csv` with one row per station/DOY. 12 stations × 366 DOYs = 4,392 rows. Trivial.

---

## 3. Climatology Normals (NOAA NCEI — Free)

### Source

NOAA NCEI 1991-2020 Climate Normals:
- **URL:** https://www.ncei.noaa.gov/access/us-climate-normals/
- **Bulk download:** https://www.ncei.noaa.gov/data/normals-daily/1991-2020/
- **Format:** CSV by station, one file per station
- **Fields:** Daily normal high temp, low temp, precipitation, heating/cooling degree days, and standard deviations

### How to integrate

**For training (one-time build):**

```bash
# Download all daily normals for our stations
# Example for KATL (USW00013874 is the WBAN ID)
BASE="https://www.ncei.noaa.gov/data/normals-daily/1991-2020/access"
for wban in USW00013874 USW00094846 ...; do
    curl -O "$BASE/$wban.csv"
done
```

Store in `data/climo/station_normals.parquet` with columns:
```
station, doy, normal_high, normal_low, std_high, std_low, 
precip_normal, hdd_normal, cdd_normal
```

**For live inference:**
No real-time API call. The normals are static. Load the parquet file at startup, join on `(station, doy)` to get:
```python
features["climo_avg_high"] = normals["normal_high"]
features["climo_std_high"] = normals["std_high"]
features["departure_from_climo"] = current_temp - normals["normal_high"]
features["yesterday_high_departure"] = ...  # from yesterday's resolved outcome
```

### Alternative: Compute your own normals

Since you have 2022-2025 ASOS data, compute station-specific "recent normals":
```python
# Per station, per DOY, average final_high_tmpf across 2022-2024
climo = df.groupby(['station', df['local_date'].dt.dayofyear])['final_high_tmpf'] \
          .agg(['mean', 'std', lambda x: x.quantile(0.1), lambda x: x.quantile(0.9)])
```

This is "wrong" compared to 30-year normals BUT it's specific to your training period, which means no look-ahead bias risk. Arguably better for ML purposes.

---

## 4. HRRR V2 Enrichment (Already Built, Needs Wiring)

### Status

| Component | Status |
|-----------|--------|
| HRRR V2 cache framework (`hrrr_v2.py`) | Built |
| HRRR V2 extraction pipeline (`build_hrrr_v2_cache.py`) | Built |
| Trained HRRR models (6 model types) | Built — in `data/models/` |
| HRRR point forecast cache (SQLite) | May or may not be populated |
| Research loop integration | **NOT DONE** |
| Live loop integration | **NOT DONE** (live HRRR uses NOMADS real-time API, not V2 cache) |

### Check cache status

```bash
sqlite3 ~/.local/state/roboweather/hrrr_v2_cache.sqlite \
  "select status, count(*) from hrrr_extract_tasks group by status;"
```

### How to enable HRRR in the research loop

The research loop needs two things:
1. **HRRR features in prediction_snapshots**: The snapshot collector already records `hrrr_remaining_max`, `hrrr_current_temp`, etc. in the signal dictionary. These come from the real-time HRRR client (NOMADS), not the V2 archive cache. So live snapshots already have HRRR features.

2. **HRRR MODELS loaded**: The research loop loads specific model sets. Currently only observation-based models are loaded. Need to also load HRRR models from `MODEL_FAMILIES["hrrr_v2"]`.

The change is in the research loop initialization (likely in `weather_trader/cli.py` or the collector). Add:
```python
from weather_trader.research.policies import MODEL_FAMILIES
# Also load HRRR models
for alias, model_name in MODEL_FAMILIES["hrrr_v2"].items():
    load_model(model_name)
```

### Enriched HRRR features for live (from NOMADS real-time API)

The live HRRR client (`hrrr_client.py`) already fetches point forecasts. Currently it returns:
```python
hrrr_current_temp, hrrr_remaining_max, hrrr_cloud_cover_next_3h, hrrr_wind_speed_next_3h
```

Can enrich by also fetching (all available in HRRR 2D GRIB2):
- `tmp2m` at forecast hours 0,1,2,3,6,12,18 (temperature trajectory)
- `dp2m` (dewpoint trajectory)  
- `rh2m` (relative humidity)
- `gust` (wind gust)
- `crain` (categorical rain — yes/no for convection)
- `hpbl` (planetary boundary layer height — only in HRRR, not HRRR 2D subset)

However, the NOMADS filter script (`filter_hrrr_2d.pl`) has limited field support. For more fields, direct GRIB2 download + extraction (like the V2 pipeline does for archive) would be needed for live. This is higher effort but unlocks the atmospheric profile features.

---

## 5. GFS Global Model (NOMADS — Free)

### Source

GFS 0.25-degree forecasts from NOAA NOMADS:
- **URL:** https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25_1hr.pl
- **Format:** GRIB2, similar to HRRR
- **Resolution:** 0.25° × 0.25° (~28 km at mid-latitudes)
- **Runs:** 4x daily (00z, 06z, 12z, 18z)
- **Forecast horizon:** 384 hours
- **Key advantage over HRRR:** Global coverage (works for international stations), different physics → orthogonal signal

### Fields of interest

Same point extraction approach as HRRR V2, applied to GFS:
```
tmp2m, dp2m, rh2m, ugrd10m, vgrd10m, gust, tcdc, dswrf,
pres_msl, hpbl, cape, pwat, soilw
```

### Integration approach

Reuse the HRRR V2 extraction pipeline pattern:
1. Download GFS GRIB2 index files for cycles matching snapshot times
2. Parse index for relevant variable/level records
3. Byte-range download and pygrib decode at station lat/lon
4. Store in a similar SQLite cache: `gfs_v1_cache.sqlite`
5. Build feature materializer similar to `materialize_hrrr_v2_features()`
6. Train GFS-enriched models

Effort: ~2-3 days (mostly copy-paste from HRRR pipeline with different URLs and variable names).

---

## 6. Soil Moisture (NASA SMAP — Free)

### Source

NASA Soil Moisture Active Passive (SMAP) Level 4:
- **URL:** https://nsidc.org/data/smap/smap-data.html
- **Format:** HDF5, 9km resolution, daily
- **Latency:** ~24 hours (not real-time, but soil moisture changes slowly)
- **Fields:** Surface soil moisture (0-5 cm), root zone soil moisture (0-100 cm)

### Integration

This is a **backfill-only** feature — the 24-hour latency means it won't help same-day live decisions, but it's useful for training because it captures the antecedent soil state. Dry soil = more sensible heating, wet soil = more evaporative cooling.

```python
# For training: match each station/date with the previous day's SMAP soil moisture
# at the nearest 9km grid cell
# Use NSIDC's EarthAccess API or direct HTTPS download

# Simpler alternative: use GFS 0-10cm soil moisture from the GFS analysis
# (same pipeline as section 5, field "soill_0_10cm")
```

---

## 7. ECMWF ERA5 Reanalysis (Free — For Climo Backfill)

### Source

Copernicus Climate Data Store (CDS):
- **URL:** https://cds.climate.copernicus.eu/
- **Format:** NetCDF/GRIB, 0.25° resolution
- **Latency:** ~3 months behind real-time
- **Use case:** Build station climatology from 30-year reanalysis

### Integration

One-time download of station-point time series for 1991-2020:
```python
import cdsapi
c = cdsapi.Client()
c.retrieve('reanalysis-era5-single-levels', {
    'product_type': 'reanalysis',
    'variable': ['2m_temperature', '2m_dewpoint_temperature', 
                 'total_precipitation', 'total_cloud_cover'],
    'year': [str(y) for y in range(1991, 2021)],
    'month': [str(m).zfill(2) for m in range(1, 13)],
    'day': [str(d).zfill(2) for d in range(1, 32)],
    'time': '18:00',  # approximate max temp time in UTC
    'area': [lat+0.25, lon-0.25, lat-0.25, lon+0.25],  # bounding box around station
    'format': 'netcdf',
}, 'station_era5.nc')
```

This gives you 30 years of daily maximum temperature estimates per station. Compute:
- DOY-average max temp (climo normal)
- DOY standard deviation
- 10th/90th percentiles
- Regional temperature anomaly correlation structure (for regime detection)

Effort: ~4 hours for one-time download + processing.

---

## 8. Practical Integration Plan

### Phase 1: Zero-Dependency Wins (Today)

| # | Feature | Source | Training | Live |
|---|---------|--------|----------|------|
| 1 | `relative_humidity` | Already in IEM fetch | Add to feature row dict | Add to StationWeatherState |
| 2 | `pressure_mslp` | Already in IEM fetch | Add to feature row dict | Add to StationWeatherState |
| 3 | `wet_bulb_approx` | Compute from T + Td/RH | Add to prepare_station_observations | Add to WeatherFeatureService |
| 4 | `temp_range_so_far` | `max_so_far - min_so_far` | Add to _make_feature_row | Already have both values |
| 5 | `visibility_miles` | Add `vsby` to IEM DEFAULT_FIELDS | Add to feature builder | Add to StationWeatherState |
| 6 | `precip_1h_in` | Add `p01i` to IEM DEFAULT_FIELDS | Add to feature builder | Add to StationWeatherState |

**Total effort:** ~50 lines of Python. **Expected IC gain:** +0.03-0.05.

### Phase 2: Computed Features (Tomorrow)

| # | Feature | Source | Dependencies |
|---|---------|--------|-------------|
| 7 | `hour_since_sunrise` | Solar geometry + station tz | Precompute sunrise table |
| 8 | `solar_noon_proximity` | Solar geometry | Same precomputed table |
| 9 | `pressure_tendency_3h` | Same as temp_change_3h approach | Use existing lookback pattern |
| 10 | Station-specific climo normals | Compute from 2022-2024 data | Already have the data |

**Total effort:** ~100 lines. **Expected IC gain:** +0.02-0.03.

### Phase 3: External Data (This Week)

| # | Feature | Source | Effort |
|---|---------|--------|--------|
| 11 | NOAA 1991-2020 normals | NCEI bulk download | 2 hours |
| 12 | HRRR model loading in research loop | Models already trained | 1 hour |
| 13 | HRRR atmospheric fields (850mb, 500mb, etc.) | HRRR V2 archive cache | 4 hours (cache build + feature join) |

**Total effort:** 1 day. **Expected IC gain:** +0.04-0.08.

### Phase 4: New Models (1-2 Weeks)

| # | Source | Effort |
|---|--------|--------|
| 14 | GFS 0.25-degree point forecasts | 2-3 days (copy HRRR pipeline) |
| 15 | GFS soil moisture | Included in GFS pipeline |
| 16 | ERA5 climo backfill for international stations | 4 hours one-time |

---

## 9. Live Inference: What Changes

For live trading, the `WeatherFeatureService.get_state()` method in `weather_trader/execution/weather.py` is the integration point. It:

1. Fetches today's IEM ASOS observations (already includes `relh`, `mslp` — just need to use them)
2. Runs `prepare_station_observations()` (need to add field processing there)
3. Extracts the latest row as `StationWeatherState` (need to add new fields to the dataclass)
4. Fetches live HRRR via `HRRRClient` (need to enrich returned dict)

The `FairValueEngine` then builds a feature vector from `StationWeatherState` + market context and feeds it to the model. Any new field in `StationWeatherState` needs a corresponding entry in the feature-building logic there.

**Key constraint:** Live inference must stay fast (<5 seconds per station). Adding fields to the existing fetch costs zero extra HTTP calls.

---

## 10. Key References

- **IEM ASOS field list:** https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?help (shows all available `data=` parameters)
- **NOAA NCEI Climate Normals:** https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals
- **HRRR NOMADS:** https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/
- **GFS NOMADS:** https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/
- **ERA5 CDS:** https://cds.climate.copernicus.eu/cdsapp#!/dataset/reanalysis-era5-single-levels
- **SMAP:** https://nsidc.org/data/SPL4SMGP/versions/7
- **Solar geometry (NOAA):** https://gml.noaa.gov/grad/solcalc/
