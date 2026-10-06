"""Choose a projected CRS before any length or area is calculated.

UTM is a good fit for a survey that sits inside one zone. It is a poor fit
for a country-sized polygon, and it is not defined near the poles. Those
cases get their own projection in `measurement_crs`.
"""

import math

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

# UTM zones are 6 degrees wide and run from 80°S to 84°N.
UTM_MAX_WIDTH_DEG = 6.0
UTM_MAX_HEIGHT_DEG = 12.0
UTM_MAX_NORTH_LAT = 84.0
UTM_MIN_SOUTH_LAT = -80.0


class CRSError(ValueError):
    """The caller passed a CRS string pyproj could not use."""


def utm_epsg(lon: float, lat: float) -> int:
    """Standard UTM zone. This ignores the Norway and Svalbard exceptions."""
    lon = ((lon + 180.0) % 360.0) - 180.0
    if lon == 180.0:
        lon = -180.0
    zone = int((lon + 180.0) / 6.0) + 1
    zone = min(60, max(1, zone))
    base = 32600 if lat >= 0 else 32700
    return base + zone


def normalize_crs(crs: CRS) -> CRS:
    current = crs
    if current.is_bound and current.source_crs is not None:
        current = current.source_crs
    if current.is_compound and current.sub_crs_list:
        current = current.sub_crs_list[0]
    return current


def parse_crs(text: str) -> CRS:
    try:
        crs = CRS.from_user_input(text.strip())
    except Exception as exc:
        raise CRSError(f"Could not understand CRS {text!r}.") from exc
    return normalize_crs(crs)


def crs_label(crs: CRS | None) -> str | None:
    if crs is None:
        return None
    authority = crs.to_authority()
    if authority and authority[0] and authority[1]:
        return f"{authority[0]}:{authority[1]}"
    code = crs.to_epsg()
    if code:
        return f"EPSG:{code}"
    name = (crs.name or "").strip()
    if name and name.lower() != "unknown":
        return name
    try:
        return crs.to_proj4()
    except Exception:
        return crs.to_string()


def metres_per_unit(crs: CRS) -> float | None:
    """How many metres one CRS unit represents, when both horizontal axes agree."""
    axes = list(crs.axis_info or [])
    if len(axes) < 2:
        return None
    factors: list[float] = []
    for axis in axes[:2]:
        unit = (axis.unit_name or "").lower()
        if unit in {"degree", "degrees", "radian", "radians"}:
            return None
        factor = axis.unit_conversion_factor
        if not factor:
            return None
        factors.append(float(factor))
    if abs(factors[0] - factors[1]) > 1e-9:
        return None
    return factors[0]


def _wgs84_transformer(source: CRS) -> Transformer:
    return Transformer.from_crs(source, "EPSG:4326", always_xy=True)


def wgs84_anchor(geometry: BaseGeometry, source: CRS) -> tuple[float, float]:
    transformer = _wgs84_transformer(source)
    try:
        point = geometry.representative_point()
        x, y = float(point.x), float(point.y)
    except Exception:
        minx, miny, maxx, maxy = geometry.bounds
        x = (minx + maxx) / 2
        y = (miny + maxy) / 2
    lon, lat = transformer.transform(x, y)
    lon = float(lon)
    lat = float(lat)
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise ValueError("Geometry has no usable coordinates for choosing a projection.")
    return lon, lat


def wgs84_bounds(geometry: BaseGeometry, source: CRS) -> tuple[float, float, float, float]:
    transformer = _wgs84_transformer(source)
    minx, miny, maxx, maxy = geometry.bounds
    corners = ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy))
    projected = [transformer.transform(x, y) for x, y in corners]
    xs = [float(point[0]) for point in projected]
    ys = [float(point[1]) for point in projected]
    return min(xs), min(ys), max(xs), max(ys)


def polar_crs(lat: float) -> tuple[CRS, str]:
    epsg = 32661 if lat >= 0 else 32761
    try:
        return CRS.from_epsg(epsg), f"EPSG:{epsg}"
    except Exception:
        hemisphere = "90" if lat >= 0 else "-90"
        proj4 = (
            f"+proj=stere +lat_0={hemisphere} +lat_ts={hemisphere} "
            "+lon_0=0 +datum=WGS84 +units=m +no_defs"
        )
        return CRS.from_proj4(proj4), proj4


def local_azimuthal(lon: float, lat: float, kind: str) -> tuple[CRS, str]:
    # laea keeps area honest. aeqd keeps distance from the anchor honest.
    # A mixed geometry cannot have both, so area wins and the length is approximate.
    proj = "laea" if kind in {"area", "mixed"} else "aeqd"
    proj4 = f"+proj={proj} +lat_0={lat:.6f} +lon_0={lon:.6f} +datum=WGS84 +units=m +no_defs"
    return CRS.from_proj4(proj4), proj4


def measurement_crs(geometry: BaseGeometry, source: CRS, kind: str) -> tuple[CRS, str, str | None]:
    """Return the CRS to measure in, a short label, and an optional explanation."""
    source = normalize_crs(source)
    if not source.is_geographic:
        label = crs_label(source) or source.to_string()
        return source, label, None

    lon, lat = wgs84_anchor(geometry, source)
    minx, miny, maxx, maxy = wgs84_bounds(geometry, source)
    width = maxx - minx
    height = maxy - miny

    if lat > UTM_MAX_NORTH_LAT or lat < UTM_MIN_SOUTH_LAT:
        crs, label = polar_crs(lat)
        return (
            crs,
            label,
            "This geometry is outside the UTM latitude range, "
            "so Universal Polar Stereographic was used.",
        )

    if width > UTM_MAX_WIDTH_DEG or height > UTM_MAX_HEIGHT_DEG:
        crs, label = local_azimuthal(lon, lat, kind)
        note = (
            "This geometry is larger than a single UTM zone, "
            "so a local azimuthal projection was used."
        )
        if width > 180:
            note += (
                " The bounding box spans more than 180 degrees, so treat the value as approximate."
            )
        return crs, label, note

    epsg = utm_epsg(lon, lat)
    return CRS.from_epsg(epsg), f"EPSG:{epsg}", None


def project_geometry(geometry: BaseGeometry, source: CRS, target: CRS) -> BaseGeometry:
    source_code = source.to_epsg()
    target_code = target.to_epsg()
    if source_code is not None and source_code == target_code:
        return geometry
    transformer = Transformer.from_crs(source, target, always_xy=True)
    return transform(transformer.transform, geometry)
