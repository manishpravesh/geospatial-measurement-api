"""Read KML placemarks without depending on a GDAL build.

KML 2.2 stores coordinates as longitude, latitude, and an optional altitude,
in WGS 84. The reader keeps the geometry and the placemark attributes. It
does not measure anything.
"""

import logging
from pathlib import Path

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import parse as safe_parse
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.exceptions import FileContentError
from app.services.datasets import LoadedDataset, LoadedFeature
from app.services.serialize import clip_text

logger = logging.getLogger(__name__)

GEOMETRY_TAGS = {
    "Point",
    "LineString",
    "LinearRing",
    "Polygon",
    "MultiGeometry",
    "Track",
    "MultiTrack",
}
CONTAINERS = {"kml", "Document", "Folder"}


def local_name(element) -> str:
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def direct_children(element, name: str):
    return [child for child in list(element) if local_name(child) == name]


def first_child(element, name: str):
    for child in element:
        if local_name(child) == name:
            return child
    return None


def child_text(element, name: str) -> str | None:
    node = first_child(element, name)
    if node is None or not node.text:
        return None
    text = node.text.strip()
    return text or None


def parse_positions(text: str) -> list[tuple[float, ...]]:
    positions: list[tuple[float, ...]] = []
    if not text:
        return positions
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = [part for part in token.split(",") if part != ""]
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if len(parts) >= 3:
            try:
                positions.append((lon, lat, float(parts[2])))
                continue
            except ValueError:
                pass
        positions.append((lon, lat))
    return positions


def coordinates_text(element) -> str:
    for node in element.iter():
        if local_name(node) == "coordinates" and node.text:
            return node.text
    return ""


def close_ring(positions: list[tuple[float, ...]]) -> list[tuple[float, ...]] | None:
    if len(positions) < 3:
        return None
    if positions[0][0] != positions[-1][0] or positions[0][1] != positions[-1][1]:
        positions = [*positions, positions[0]]
    if len(positions) < 4:
        return None
    return positions


def combine(parts: list[BaseGeometry]) -> BaseGeometry | None:
    flat: list[BaseGeometry] = []
    for geom in parts:
        if geom.geom_type == "GeometryCollection" or geom.geom_type.startswith("Multi"):
            flat.extend(list(geom.geoms))
        else:
            flat.append(geom)
    if not flat:
        return None
    if len(flat) == 1:
        return flat[0]
    types = {geom.geom_type for geom in flat}
    if types <= {"Polygon"}:
        return MultiPolygon(flat)
    if types <= {"LineString"}:
        return MultiLineString(flat)
    if types <= {"Point"}:
        return MultiPoint(flat)
    return GeometryCollection(flat)


def parse_track(element) -> tuple[BaseGeometry | None, str | None]:
    positions: list[tuple[float, ...]] = []
    for node in element.iter():
        if local_name(node) != "coord" or not node.text:
            continue
        bits = node.text.split()
        if len(bits) < 2:
            continue
        try:
            lon = float(bits[0])
            lat = float(bits[1])
        except ValueError:
            continue
        if len(bits) >= 3:
            try:
                positions.append((lon, lat, float(bits[2])))
                continue
            except ValueError:
                pass
        positions.append((lon, lat))
    if len(positions) < 2:
        return None, "Track does not have enough coordinates."
    try:
        return LineString(positions), None
    except Exception:
        return None, "Track coordinates could not be read."


def build_polygon(
    shell: list[tuple[float, ...]], holes: list[list[tuple[float, ...]]]
) -> tuple[BaseGeometry | None, str | None]:
    try:
        return Polygon(shell, holes), None
    except Exception:
        if not holes:
            return None, "Polygon coordinates could not be read."
        try:
            return Polygon(shell), "Polygon holes could not be read and were skipped."
        except Exception:
            return None, "Polygon coordinates could not be read."


def parse_geometry(element) -> tuple[BaseGeometry | None, str | None]:
    tag = local_name(element)
    if tag == "Point":
        positions = parse_positions(coordinates_text(element))
        if not positions:
            return None, "Point has no coordinates."
        try:
            if len(positions) == 1:
                return Point(positions[0]), None
            return MultiPoint([Point(position) for position in positions]), None
        except Exception:
            return None, "Point coordinates could not be read."

    if tag in {"LineString", "LinearRing"}:
        positions = parse_positions(coordinates_text(element))
        if len(positions) < 2:
            return None, f"{tag} does not have enough coordinates."
        try:
            return LineString(positions), None
        except Exception:
            return None, f"{tag} coordinates could not be read."

    if tag == "Polygon":
        shell = None
        holes = []
        for child in list(element):
            name = local_name(child)
            if name == "outerBoundaryIs" and shell is None:
                shell = close_ring(parse_positions(coordinates_text(child)))
            elif name == "innerBoundaryIs":
                ring = close_ring(parse_positions(coordinates_text(child)))
                if ring:
                    holes.append(ring)
        if not shell:
            return None, "Polygon is missing an outer boundary."
        return build_polygon(shell, holes)

    if tag == "MultiGeometry":
        parts: list[BaseGeometry] = []
        warnings: list[str] = []
        for child in list(element):
            if local_name(child) not in GEOMETRY_TAGS:
                continue
            geometry, warning = parse_geometry(child)
            if warning:
                warnings.append(warning)
            if geometry is not None:
                parts.append(geometry)
        warning_text = "; ".join(warnings) or None
        if not parts:
            return None, warning_text or "MultiGeometry has no readable geometry."
        return combine(parts), warning_text

    if tag == "Track":
        return parse_track(element)

    if tag == "MultiTrack":
        parts = []
        warnings = []
        for child in element.iter():
            if local_name(child) != "Track":
                continue
            geometry, warning = parse_track(child)
            if warning:
                warnings.append(warning)
            if geometry is not None:
                parts.append(geometry)
        warning_text = "; ".join(warnings) or None
        if not parts:
            return None, warning_text or "MultiTrack has no readable track."
        return combine(parts), warning_text

    return None, f"Unsupported KML geometry {tag}." if tag else "Unsupported KML geometry."


def placemark_geometry(placemark) -> tuple[BaseGeometry | None, str | None]:
    parts: list[BaseGeometry] = []
    warnings: list[str] = []
    for child in list(placemark):
        if local_name(child) not in GEOMETRY_TAGS:
            continue
        geometry, warning = parse_geometry(child)
        if warning:
            warnings.append(warning)
        if geometry is not None:
            parts.append(geometry)
    warning_text = "; ".join(dict.fromkeys(warnings)) or None
    if not parts:
        return None, warning_text
    try:
        return combine(parts), warning_text
    except Exception:
        return None, warning_text or "Placemark geometry could not be combined."


def extended_properties(placemark) -> dict:
    properties: dict = {}
    for block in direct_children(placemark, "ExtendedData"):
        for node in block.iter():
            tag = local_name(node)
            if tag not in {"Data", "SimpleData"}:
                continue
            key = (node.attrib.get("name") or "").strip()
            if not key or key in properties:
                continue
            if tag == "Data":
                value_node = first_child(node, "value")
                raw = value_node.text if value_node is not None else None
            else:
                raw = node.text
            properties[key] = raw.strip() if raw and raw.strip() else None
    return properties


def placemark_properties(placemark, folders: tuple[str, ...]) -> dict:
    properties: dict = {}
    name = child_text(placemark, "name")
    if name:
        properties["name"] = clip_text(name)
    description = child_text(placemark, "description")
    if description:
        properties["description"] = clip_text(description)
    if folders:
        properties["folder"] = clip_text(" / ".join(folders))
    for key, value in extended_properties(placemark).items():
        stored = clip_text(value) if isinstance(value, str) else value
        if key in properties:
            properties[f"data_{key}"] = stored
        else:
            properties[key] = stored
    return properties


def iter_placemarks(element, folders: tuple[str, ...] = ()):
    tag = local_name(element)
    if tag == "Placemark":
        yield element, folders
        return
    if tag not in CONTAINERS:
        return
    next_folders = folders
    if tag == "Folder":
        name = child_text(element, "name")
        if name:
            next_folders = folders + (name,)
    for child in list(element):
        yield from iter_placemarks(child, next_folders)


def document_name(root) -> str | None:
    for node in root.iter():
        if local_name(node) == "Document":
            return child_text(node, "name")
    return None


def find_kml_root(root):
    if local_name(root) == "kml":
        return root
    for node in root.iter():
        if local_name(node) == "kml":
            return node
    return None


def read_kml(path: Path) -> LoadedDataset:
    try:
        root = safe_parse(path).getroot()
    except ParseError as exc:
        raise FileContentError(f"KML is not valid XML: {exc}") from exc
    except DefusedXmlException as exc:
        # The parser names the entity target. Keep that in the log, not the response.
        logger.warning("rejected unsafe KML %s: %s", path.name, exc)
        raise FileContentError("KML was rejected because it contains unsafe XML.") from exc
    except Exception as exc:
        raise FileContentError(f"Could not read KML: {exc}") from exc

    kml_root = find_kml_root(root)
    if kml_root is None:
        raise FileContentError("The file is not a KML document.")

    features: list[LoadedFeature] = []
    for placemark, folders in iter_placemarks(kml_root):
        geometry, warning = placemark_geometry(placemark)
        features.append(
            LoadedFeature(
                geometry=geometry,
                properties=placemark_properties(placemark, folders),
                warning=warning,
            )
        )
    notes = [
        "KML is WGS 84. The file CRS is EPSG:4326, including when the document does not say so.",
    ]
    if not features:
        notes.append("KML document has no placemarks.")
    return LoadedDataset(
        crs=CRS.from_epsg(4326),
        features=features,
        notes=notes,
        layer_name=document_name(kml_root) or path.stem,
    )
