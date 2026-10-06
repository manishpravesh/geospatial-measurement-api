from dataclasses import dataclass, field

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


@dataclass
class LoadedFeature:
    geometry: BaseGeometry | None
    properties: dict
    warning: str | None = None


@dataclass
class LoadedDataset:
    crs: CRS | None
    features: list[LoadedFeature]
    notes: list[str] = field(default_factory=list)
    layer_name: str | None = None
