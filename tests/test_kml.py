from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from app.services.kml import read_kml


def write_kml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_placemark_keeps_folder_attributes_and_holes(tmp_path):
    path = write_kml(
        tmp_path / "parcels.kml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Document>
            <name>Survey</name>
            <Folder>
              <name>Block A</name>
              <Placemark>
                <name>Café plot</name>
                <description>north edge</description>
                <ExtendedData>
                  <Data name="parcel_id"><value>A-1</value></Data>
                </ExtendedData>
                <Polygon>
                  <outerBoundaryIs>
                    <LinearRing>
                      <coordinates>
                        77.50,12.90 77.51,12.90 77.51,12.91 77.50,12.91 77.50,12.90
                      </coordinates>
                    </LinearRing>
                  </outerBoundaryIs>
                  <innerBoundaryIs>
                    <LinearRing>
                      <coordinates>
                        77.502,12.902 77.504,12.902 77.504,12.904 77.502,12.904 77.502,12.902
                      </coordinates>
                    </LinearRing>
                  </innerBoundaryIs>
                </Polygon>
              </Placemark>
            </Folder>
          </Document>
        </kml>
        """,
    )
    dataset = read_kml(path)
    assert dataset.layer_name == "Survey"
    assert dataset.crs.to_epsg() == 4326
    assert len(dataset.features) == 1
    feature = dataset.features[0]
    assert feature.properties["name"] == "Café plot"
    assert feature.properties["folder"] == "Block A"
    assert feature.properties["parcel_id"] == "A-1"
    assert isinstance(feature.geometry, Polygon)
    assert len(feature.geometry.interiors) == 1


def test_point_altitude_is_kept_on_the_geometry(tmp_path):
    path = write_kml(
        tmp_path / "site.kml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Placemark>
            <Point><coordinates>77.5,12.9,842</coordinates></Point>
          </Placemark>
        </kml>
        """,
    )
    feature = read_kml(path).features[0]
    assert isinstance(feature.geometry, Point)
    assert feature.geometry.has_z
    assert feature.geometry.z == 842


def test_track_becomes_a_line(tmp_path):
    path = write_kml(
        tmp_path / "walk.kml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
          <Placemark>
            <gx:Track>
              <gx:coord>77.50 12.90 10</gx:coord>
              <gx:coord>77.51 12.91 12</gx:coord>
            </gx:Track>
          </Placemark>
        </kml>
        """,
    )
    feature = read_kml(path).features[0]
    assert feature.geometry.geom_type == "LineString"
    assert len(feature.geometry.coords) == 2


def test_multigeometry_of_polygons(tmp_path):
    path = write_kml(
        tmp_path / "multi.kml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Placemark>
            <MultiGeometry>
              <Polygon>
                <outerBoundaryIs><LinearRing>
                  <coordinates>0,0 0,1 1,1 1,0 0,0</coordinates>
                </LinearRing></outerBoundaryIs>
              </Polygon>
              <Polygon>
                <outerBoundaryIs><LinearRing>
                  <coordinates>2,0 2,1 3,1 3,0 2,0</coordinates>
                </LinearRing></outerBoundaryIs>
              </Polygon>
            </MultiGeometry>
          </Placemark>
        </kml>
        """,
    )
    assert read_kml(path).features[0].geometry.geom_type == "MultiPolygon"


def test_empty_document_is_a_valid_file_with_no_features(tmp_path):
    path = write_kml(
        tmp_path / "empty.kml",
        """<?xml version="1.0" encoding="UTF-8"?>
        <kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>
        """,
    )
    dataset = read_kml(path)
    assert dataset.features == []
    assert any("no placemarks" in note for note in dataset.notes)


def test_plain_xml_is_rejected(tmp_path):
    path = write_kml(tmp_path / "notes.kml", "<note>hello</note>")
    from app.exceptions import FileContentError

    try:
        read_kml(path)
    except FileContentError as exc:
        assert "not a KML" in str(exc)
    else:
        raise AssertionError("expected the file to be rejected")


def test_broken_xml_is_rejected(tmp_path):
    path = write_kml(tmp_path / "broken.kml", "<kml><Document>")
    from app.exceptions import FileContentError

    try:
        read_kml(path)
    except FileContentError as exc:
        assert "not valid XML" in str(exc)
    else:
        raise AssertionError("expected the file to be rejected")


def test_external_entity_is_not_read(tmp_path):
    path = write_kml(
        tmp_path / "entity.kml",
        """<?xml version="1.0"?>
        <!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///C:/Windows/win.ini">]>
        <kml xmlns="http://www.opengis.net/kml/2.2">
          <Placemark>
            <name>&xxe;</name>
            <Point><coordinates>0,0</coordinates></Point>
          </Placemark>
        </kml>
        """,
    )
    from app.exceptions import FileContentError

    with pytest.raises(FileContentError, match="unsafe XML") as caught:
        read_kml(path)
    message = str(caught.value).lower()
    assert "mci extensions" not in message
    assert "[fonts]" not in message
