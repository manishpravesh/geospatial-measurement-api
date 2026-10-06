from pathlib import Path

from pyproj import CRS

from app.exceptions import FileContentError
from app.services.crs import normalize_crs
from app.services.datasets import LoadedDataset, LoadedFeature
from app.services.serialize import json_safe


def coerce_geometry(geometry):
    if geometry is None or not hasattr(geometry, "geom_type"):
        return None
    return geometry


def read_shapefile(path: Path) -> LoadedDataset:
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise FileContentError(
            "Shapefile reading is unavailable because geopandas is not installed."
        ) from exc

    try:
        frame = gpd.read_file(path)
    except Exception as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else "unknown error"
        raise FileContentError(f"Could not read the shapefile: {message}") from exc

    notes: list[str] = []
    crs = None
    if frame.crs is None:
        notes.append(
            "Shapefile has no CRS (.prj missing or unreadable). "
            "Measurements were skipped. Resubmit with assume_crs if you know the coordinate system."
        )
    else:
        try:
            crs = normalize_crs(CRS.from_user_input(frame.crs))
        except Exception:
            notes.append("Shapefile .prj could not be parsed. Measurements were skipped.")
            crs = None

    geometries = [coerce_geometry(geom) for geom in frame.geometry]
    table = frame.drop(columns=frame.geometry.name)
    if len(table.columns) == 0:
        records = [{} for _ in geometries]
    else:
        records = table.to_dict(orient="records")
        if len(records) != len(geometries):
            records = [{} for _ in geometries]

    features = [
        LoadedFeature(
            geometry=geom,
            properties={str(key): json_safe(value) for key, value in props.items()},
        )
        for geom, props in zip(geometries, records, strict=True)
    ]
    return LoadedDataset(
        crs=crs,
        features=features,
        notes=notes,
        layer_name=path.stem,
    )
