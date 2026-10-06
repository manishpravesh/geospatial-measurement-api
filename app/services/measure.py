"""Area and length in metres.

Geographic coordinates are projected first. A degree of longitude is not a
unit of distance, and it shrinks as you leave the equator, so measuring the
geometry in place would be a confident wrong answer.
"""

import math
from dataclasses import dataclass

from shapely import force_2d, make_valid
from shapely.geometry.base import BaseGeometry

from app.services.crs import measurement_crs, metres_per_unit, project_geometry

AREA_TYPES = {"Polygon", "MultiPolygon"}
LENGTH_TYPES = {"LineString", "MultiLineString", "LinearRing"}
POINT_TYPES = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    type: str
    value: float | None = None
    unit: str | None = None
    calculated_in: str | None = None
    note: str | None = None
    area_square_meters: float | None = None
    length_meters: float | None = None


def _note(parts: list[str]) -> str | None:
    cleaned = [part for part in parts if part]
    if not cleaned:
        return None
    return " ".join(cleaned)


def _round_metres(value: float) -> float | None:
    if not math.isfinite(value):
        return None
    return round(float(value), 3)


def classify(geometry: BaseGeometry) -> str:
    geom_type = geometry.geom_type
    if geom_type in AREA_TYPES:
        return "area"
    if geom_type in LENGTH_TYPES:
        return "length"
    if geom_type in POINT_TYPES:
        return "point"
    if geom_type != "GeometryCollection":
        return "unsupported"

    kinds: set[str] = set()
    for part in geometry.geoms:
        kind = classify(part)
        if kind == "mixed":
            kinds.update({"area", "length"})
        elif kind in {"area", "length"}:
            kinds.add(kind)
    if kinds == {"area", "length"}:
        return "mixed"
    if len(kinds) == 1:
        return next(iter(kinds))
    if any(part.geom_type in POINT_TYPES for part in geometry.geoms):
        return "point"
    return "unsupported"


def _area(geometry: BaseGeometry) -> float:
    if geometry.geom_type in AREA_TYPES:
        return float(geometry.area)
    if geometry.geom_type == "GeometryCollection":
        return sum(_area(part) for part in geometry.geoms)
    return 0.0


def _length(geometry: BaseGeometry) -> float:
    if geometry.geom_type in LENGTH_TYPES:
        return float(geometry.length)
    if geometry.geom_type == "GeometryCollection":
        return sum(_length(part) for part in geometry.geoms)
    return 0.0


def measure_geometry(geometry, crs) -> Measurement:
    """Measure one feature. Unsupported or broken geometries return a note, not an exception."""
    try:
        return _measure(geometry, crs)
    except Exception as exc:
        return Measurement(type="none", note=f"Could not calculate a measurement: {exc}")


def _measure(geometry, crs) -> Measurement:
    if geometry is None or not hasattr(geometry, "geom_type"):
        return Measurement(type="none", note="Feature has no geometry.")
    if geometry.is_empty:
        return Measurement(type="none", note="Geometry is empty.")

    notes: list[str] = []
    if getattr(geometry, "has_z", False):
        notes.append("Altitude was ignored; the measurement is horizontal.")

    working = force_2d(geometry)
    if not working.is_valid:
        working = make_valid(working)
        notes.append("Geometry was invalid and was repaired before measuring.")
        if working.is_empty:
            return Measurement(
                type="none",
                note=_note([*notes, "Geometry is invalid and could not be repaired."]),
            )

    kind = classify(working)
    if kind == "point":
        notes.append("Points have no area or length.")
        return Measurement(type="none", note=_note(notes))
    if kind == "unsupported":
        notes.append(f"{working.geom_type} is not supported for measurement.")
        return Measurement(type="none", note=_note(notes))
    if crs is None:
        notes.append("No CRS on this file, so area and length were not calculated.")
        return Measurement(type=kind, note=_note(notes))

    target, label, choice_note = measurement_crs(working, crs, kind)
    if choice_note:
        notes.append(choice_note)

    projected = project_geometry(working, crs, target)
    factor = metres_per_unit(target)
    units_known = factor is not None
    if not units_known:
        factor = 1.0
        area_unit = "square_crs_units"
        length_unit = "crs_units"
        notes.append(
            "CRS axis units were not recognized, so the value was not converted to metres."
        )
    else:
        area_unit = "square_meters"
        length_unit = "meters"

    area = None
    length = None
    if kind in {"area", "mixed"}:
        area = _round_metres(_area(projected) * factor * factor)
        if area is None:
            notes.append("Area was not a finite number.")
        elif area == 0:
            notes.append("Area is zero. The ring may be degenerate.")
    if kind in {"length", "mixed"}:
        length = _round_metres(_length(projected) * factor)
        if length is None:
            notes.append("Length was not a finite number.")
        elif length == 0:
            notes.append("Length is zero.")

    if kind == "area" and area is None:
        return Measurement(type="none", calculated_in=label, note=_note(notes))
    if kind == "length" and length is None:
        return Measurement(type="none", calculated_in=label, note=_note(notes))

    if kind == "area":
        return Measurement(
            type="area",
            value=area,
            unit=area_unit,
            calculated_in=label,
            note=_note(notes),
            area_square_meters=area if units_known else None,
        )
    if kind == "length":
        return Measurement(
            type="length",
            value=length,
            unit=length_unit,
            calculated_in=label,
            note=_note(notes),
            length_meters=length if units_known else None,
        )
    return Measurement(
        type="mixed",
        calculated_in=label,
        note=_note(notes),
        area_square_meters=area if units_known else None,
        length_meters=length if units_known else None,
    )
