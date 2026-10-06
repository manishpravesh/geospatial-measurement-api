import io
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import LineString, Point, box
from shapely.ops import transform

from tests.conftest import build_app

ROOT = Path(__file__).resolve().parents[1]
UTM_ZONE = 32643


def to_wgs84(geometry, source_epsg=UTM_ZONE):
    transformer = Transformer.from_crs(source_epsg, 4326, always_xy=True)
    return transform(transformer.transform, geometry)


def kml_bytes(placemarks: str, document_name: str = "sample") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{document_name}</name>
    {placemarks}
  </Document>
</kml>
""".encode()


def polygon_kml(geometry, name: str = "Parcel") -> bytes:
    coordinates = " ".join(f"{x:.8f},{y:.8f}" for x, y in geometry.exterior.coords)
    return kml_bytes(
        f"""
        <Placemark>
          <name>{name}</name>
          <Polygon>
            <outerBoundaryIs><LinearRing>
              <coordinates>{coordinates}</coordinates>
            </LinearRing></outerBoundaryIs>
          </Polygon>
        </Placemark>
        """
    )


def upload(client, filename: str, payload: bytes, **form):
    files = {"file": (filename, payload, "application/octet-stream")}
    return client.post("/api/files/", files=files, data=form or None)


def square_km():
    return box(500000, 1400000, 501000, 1401000)


def zip_shapefile(directory: Path, geometry, crs, include_prj: bool = True, name: str = "parcel"):
    folder = directory / f"build-{name}"
    folder.mkdir()
    frame = gpd.GeoDataFrame(
        {"name": ["North plot"], "plots": [3]},
        geometry=[geometry],
        crs=crs,
    )
    frame.to_file(folder / f"{name}.shp", encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in folder.iterdir():
            if not include_prj and path.suffix.lower() == ".prj":
                continue
            archive.write(path, arcname=f"survey/{path.name}")
    return buffer.getvalue()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_lists_the_measurement_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/files/" in paths
    assert "/api/files/{file_id}/" in paths
    assert "/api/files/{file_id}/measurements/" in paths


def test_kml_square_is_measured_in_metres(client):
    geographic = to_wgs84(square_km())
    created = upload(client, "square.kml", polygon_kml(geographic, name="North plot"))
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert body["filename"] == "square.kml"
    assert body["feature_count"] == 1
    assert body["crs"] == "EPSG:4326"
    assert body["layer"] == "sample"
    assert any("not treated as metres" in note for note in body["notes"])

    fetched = client.get(f"/api/files/{body['id']}/")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    measured = client.get(f"/api/files/{body['id']}/measurements/")
    assert measured.status_code == 200
    page = measured.json()
    feature = page["features"][0]
    assert feature["feature_id"] == "0"
    assert feature["geometry_type"] == "Polygon"
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["crs"] == "EPSG:4326"
    assert feature["properties"]["name"] == "North plot"
    assert feature["measurement"]["type"] == "area"
    assert feature["measurement"]["unit"] == "square_meters"
    assert feature["measurement"]["calculated_in"] == "EPSG:32643"
    assert feature["measurement"]["value"] == pytest.approx(1_000_000, rel=1e-4)
    # A degree-based area for this square is a tiny fraction. Guard the easy mistake.
    assert feature["measurement"]["value"] > 10_000
    assert page["summary"]["total_area_square_meters"] == pytest.approx(1_000_000, rel=1e-4)
    assert page["summary"]["measured_features"] == 1


def test_kml_without_the_trailing_slash(client):
    response = upload(client, "Site.KML", polygon_kml(to_wgs84(square_km())))
    # upload() posts to the slash route. Hit the alias directly too.
    alias = client.post(
        "/api/files",
        files={
            "file": (
                "Site.KML",
                polygon_kml(to_wgs84(square_km())),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 201
    assert alias.status_code == 201
    assert alias.json()["filename"] == "Site.KML"


def test_mixed_kml_reports_area_length_and_a_point(client):
    area = to_wgs84(square_km())
    line = to_wgs84(LineString([(500000, 1400000), (502500, 1400000)]))
    point = to_wgs84(Point(500000, 1400000))
    ring = " ".join(f"{x:.8f},{y:.8f}" for x, y in area.exterior.coords)
    line_coords = " ".join(f"{x:.8f},{y:.8f}" for x, y in line.coords)
    px, py = point.x, point.y
    payload = kml_bytes(
        f"""
        <Placemark><name>plot</name><Polygon><outerBoundaryIs><LinearRing>
          <coordinates>{ring}</coordinates>
        </LinearRing></outerBoundaryIs></Polygon></Placemark>
        <Placemark><name>path</name><LineString>
          <coordinates>{line_coords}</coordinates>
        </LineString></Placemark>
        <Placemark><name>pin</name><Point>
          <coordinates>{px:.8f},{py:.8f}</coordinates>
        </Point></Placemark>
        """
    )
    created = upload(client, "mixed.kml", payload)
    assert created.status_code == 201
    page = client.get(f"/api/files/{created.json()['id']}/measurements/").json()
    kinds = [feature["measurement"]["type"] for feature in page["features"]]
    assert kinds == ["area", "length", "none"]
    assert page["features"][1]["measurement"]["value"] == pytest.approx(2500, rel=1e-3)
    assert page["features"][2]["measurement"]["value"] is None
    assert page["summary"]["unmeasured_features"] == 1
    assert page["summary"]["total_length_meters"] == pytest.approx(2500, rel=1e-3)

    points = client.get(
        f"/api/files/{created.json()['id']}/measurements/",
        params={"geometry_type": "point", "include_geometry": False},
    ).json()
    assert points["matched_features"] == 1
    assert points["features"][0]["geometry"] is None
    assert points["feature_count"] == 3

    window = client.get(
        f"/api/files/{created.json()['id']}/measurements/",
        params={"offset": 1, "limit": 1},
    ).json()
    assert [feature["feature_id"] for feature in window["features"]] == ["1"]
    assert window["matched_features"] == 3


def test_shapefile_zip_polygon_and_attributes(client, tmp_path):
    payload = zip_shapefile(tmp_path, to_wgs84(square_km()), "EPSG:4326")
    created = upload(client, "parcels.zip", payload)
    assert created.status_code == 201
    body = created.json()
    assert body["crs"] == "EPSG:4326"
    assert body["layer"] == "parcel"
    page = client.get(f"/api/files/{body['id']}/measurements/").json()
    feature = page["features"][0]
    assert feature["properties"]["name"] == "North plot"
    assert feature["properties"]["plots"] == 3
    assert feature["measurement"]["value"] == pytest.approx(1_000_000, rel=1e-4)
    assert feature["measurement"]["calculated_in"] == "EPSG:32643"


def test_shapefile_without_prj_is_not_guessed(client, tmp_path):
    payload = zip_shapefile(tmp_path, square_km(), "EPSG:32643", include_prj=False)
    created = upload(client, "bare.zip", payload)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert body["crs"] is None
    page = client.get(f"/api/files/{body['id']}/measurements/").json()
    assert page["features"][0]["measurement"]["value"] is None
    assert "No CRS" in page["features"][0]["measurement"]["note"]

    supplied = upload(client, "bare.zip", payload, assume_crs="EPSG:32643")
    assert supplied.status_code == 201
    supplied_body = supplied.json()
    assert supplied_body["crs"] == "EPSG:32643"
    assert any("assume_crs" in note for note in supplied_body["notes"])
    measured = client.get(f"/api/files/{supplied_body['id']}/measurements/").json()
    assert measured["features"][0]["measurement"]["value"] == pytest.approx(1_000_000, abs=0.01)


def test_assume_crs_does_not_override_a_prj(client, tmp_path):
    payload = zip_shapefile(tmp_path, to_wgs84(square_km()), "EPSG:4326")
    created = upload(client, "has-prj.zip", payload, assume_crs="EPSG:3857")
    assert created.status_code == 201
    body = created.json()
    assert body["crs"] == "EPSG:4326"
    assert any("ignored" in note for note in body["notes"])
    measured = client.get(f"/api/files/{body['id']}/measurements/").json()
    assert measured["features"][0]["measurement"]["calculated_in"] == "EPSG:32643"


def test_kmz_is_read_as_kml(client):
    kml = polygon_kml(to_wgs84(square_km()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc.kml", kml)
    created = upload(client, "survey.kmz", buffer.getvalue())
    assert created.status_code == 201
    assert created.json()["crs"] == "EPSG:4326"
    assert created.json()["feature_count"] == 1


def test_empty_kml_completes_with_zero_features(client):
    payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>"""
    created = upload(client, "empty.kml", payload)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "COMPLETED"
    assert body["feature_count"] == 0
    assert body["crs"] == "EPSG:4326"


def test_bad_kml_is_stored_as_failed_and_does_not_crash(client):
    created = upload(client, "broken.kml", b"<kml><Document>")
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "FAILED"
    assert "XML" in body["error"]
    fetched = client.get(f"/api/files/{body['id']}/")
    assert fetched.json()["status"] == "FAILED"
    page = client.get(f"/api/files/{body['id']}/measurements/")
    assert page.status_code == 200
    assert page.json()["features"] == []


def test_unsafe_kml_entity_is_not_expanded(client):
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///C:/Windows/win.ini">]>
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark><name>&xxe;</name><Point><coordinates>0,0</coordinates></Point></Placemark>
    </kml>
    """
    response = upload(client, "entity.kml", payload)
    assert response.status_code != 500
    text = response.text.lower()
    assert "mci extensions" not in text
    assert "[fonts]" not in text


def test_text_file_is_rejected(client):
    response = upload(client, "notes.txt", b"hello")
    assert response.status_code == 400
    assert client.get("/api/files/").json()["files"] == []


def test_bad_zip_and_zip_slip_are_rejected(client, tmp_path):
    bad = upload(client, "nope.zip", b"not a zip")
    assert bad.status_code == 400

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../../secret.txt", b"owned")
        archive.writestr("parcel.shp", b"shp")
    slipped = upload(client, "evil.zip", buffer.getvalue())
    assert slipped.status_code == 400
    assert "escapes" in slipped.json()["detail"]
    assert not (tmp_path / "secret.txt").exists()
    assert client.get("/api/files/").json()["files"] == []


def test_unknown_assume_crs_is_rejected(client):
    response = upload(
        client,
        "square.kml",
        polygon_kml(to_wgs84(square_km())),
        assume_crs="NOT_A_CRS",
    )
    assert response.status_code == 400
    assert client.get("/api/files/").json()["files"] == []


def test_oversized_upload_is_rejected(tmp_path):
    app = build_app(tmp_path, max_upload_bytes=32)
    with TestClient(app) as client:
        response = upload(client, "square.kml", b"x" * 64)
    assert response.status_code == 413


def test_feature_limit(tmp_path):
    app = build_app(tmp_path, max_features=1)
    payload = kml_bytes(
        """
        <Placemark><Point><coordinates>0,0</coordinates></Point></Placemark>
        <Placemark><Point><coordinates>1,1</coordinates></Point></Placemark>
        """
    )
    with TestClient(app) as client:
        response = upload(client, "two.kml", payload)
    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"
    assert "limit" in response.json()["error"]


def test_missing_file_is_404(client):
    missing = "a" * 32
    assert client.get(f"/api/files/{missing}/").status_code == 404
    assert client.get(f"/api/files/{missing}/measurements/").status_code == 404
    assert client.get("/api/files/not-an-id/").status_code == 404


def test_list_and_delete(client):
    first = upload(client, "one.kml", polygon_kml(to_wgs84(square_km()))).json()
    second = upload(client, "two.kml", polygon_kml(to_wgs84(square_km()))).json()
    listed = {item["id"] for item in client.get("/api/files/").json()["files"]}
    assert {first["id"], second["id"]} <= listed

    deleted = client.delete(f"/api/files/{first['id']}/")
    assert deleted.status_code == 204
    assert client.get(f"/api/files/{first['id']}/").status_code == 404
    assert client.get(f"/api/files/{second['id']}/measurements/").status_code == 200


def test_checked_in_samples(client):
    square = ROOT / "samples" / "square_1km.kml"
    created = upload(client, square.name, square.read_bytes())
    assert created.status_code == 201
    page = client.get(f"/api/files/{created.json()['id']}/measurements/").json()
    kml_area = page["features"][0]["measurement"]
    assert kml_area["value"] == pytest.approx(1_000_000.512, abs=0.001)
    assert kml_area["calculated_in"] == "EPSG:32643"

    archive = ROOT / "samples" / "square_1km.zip"
    zipped = upload(client, archive.name, archive.read_bytes())
    assert zipped.status_code == 201
    zipped_page = client.get(f"/api/files/{zipped.json()['id']}/measurements/").json()
    measurement = zipped_page["features"][0]["measurement"]
    assert zipped_page["features"][0]["properties"]["name"] == "North plot"
    assert measurement["value"] == pytest.approx(1_000_000.0, abs=0.001)

    track = ROOT / "samples" / "track_2500m.kml"
    walked = upload(client, track.name, track.read_bytes())
    kinds = [
        feature["measurement"]["type"]
        for feature in client.get(f"/api/files/{walked.json()['id']}/measurements/").json()[
            "features"
        ]
    ]
    assert kinds == ["length", "none"]
    length = client.get(f"/api/files/{walked.json()['id']}/measurements/").json()["features"][0][
        "measurement"
    ]["value"]
    assert length == pytest.approx(2500, rel=1e-3)
