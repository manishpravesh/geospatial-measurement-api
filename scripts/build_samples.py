"""Write the KML and zipped shapefile used in the README."""

import zipfile
from pathlib import Path

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, Point, box
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
ZONE = 32643


def to_wgs84(geometry):
    transformer = Transformer.from_crs(ZONE, 4326, always_xy=True)
    return transform(transformer.transform, geometry)


def ring_text(geometry) -> str:
    return " ".join(f"{x:.8f},{y:.8f}" for x, y in geometry.exterior.coords)


def line_text(geometry) -> str:
    return " ".join(f"{x:.8f},{y:.8f}" for x, y in geometry.coords)


def kml_document(name: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name}</name>
    {body}
  </Document>
</kml>
"""


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    square = to_wgs84(box(500000, 1400000, 501000, 1401000))
    track = to_wgs84(LineString([(500000, 1400000), (502500, 1400000)]))
    pin = to_wgs84(Point(500500, 1400500))

    (SAMPLES / "square_1km.kml").write_text(
        kml_document(
            "Square kilometre",
            f"""
    <Placemark>
      <name>North plot</name>
      <description>1 km square built in UTM zone 43N, written here as WGS 84.</description>
      <Polygon>
        <outerBoundaryIs><LinearRing>
          <coordinates>{ring_text(square)}</coordinates>
        </LinearRing></outerBoundaryIs>
      </Polygon>
    </Placemark>
            """,
        ),
        encoding="utf-8",
    )
    (SAMPLES / "track_2500m.kml").write_text(
        kml_document(
            "Track",
            f"""
    <Placemark>
      <name>Access track</name>
      <LineString><coordinates>{line_text(track)}</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>Centre pin</name>
      <Point><coordinates>{pin.x:.8f},{pin.y:.8f}</coordinates></Point>
    </Placemark>
            """,
        ),
        encoding="utf-8",
    )

    folder = SAMPLES / "_build"
    folder.mkdir(exist_ok=True)
    frame = gpd.GeoDataFrame(
        {"name": ["North plot"], "plots": [3]},
        geometry=[square],
        crs="EPSG:4326",
    )
    frame.to_file(folder / "square_1km.shp", encoding="utf-8")
    with zipfile.ZipFile(SAMPLES / "square_1km.zip", "w") as archive:
        for path in folder.iterdir():
            if path.is_file():
                archive.write(path, arcname=path.name)
    for path in folder.iterdir():
        path.unlink()
    folder.rmdir()
    print(f"wrote samples in {SAMPLES}")


if __name__ == "__main__":
    main()
