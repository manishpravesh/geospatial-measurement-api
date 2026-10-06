import pytest
from pyproj import CRS, Transformer
from shapely.geometry import GeometryCollection, LineString, Point, Polygon, box
from shapely.ops import transform

from app.services.crs import utm_epsg
from app.services.measure import measure_geometry


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (0, 10, 32631),
        (-0.1, 10, 32630),
        (77.5946, 12.9716, 32643),
        (77.5946, -12.97, 32743),
        (179.9, 10, 32660),
        (-180, 10, 32601),
        (180, -5, 32701),
    ],
)
def test_utm_zone_from_longitude(lon, lat, epsg):
    assert utm_epsg(lon, lat) == epsg


def test_square_kilometre_round_trips_through_wgs84():
    """A 1 km square built in UTM must not come back as square degrees."""
    from pyproj import Geod

    source = CRS.from_epsg(32643)
    square = box(500000, 1400000, 501000, 1401000)
    to_wgs84 = Transformer.from_crs(source, 4326, always_xy=True)
    geographic = transform(to_wgs84.transform, square)

    measured = measure_geometry(geographic, CRS.from_epsg(4326))

    assert geographic.area < 1
    assert measured.type == "area"
    assert measured.unit == "square_meters"
    assert measured.calculated_in == "EPSG:32643"
    assert measured.value == pytest.approx(1_000_000, abs=1)

    geod = Geod(ellps="WGS84")
    longitudes, latitudes = zip(*geographic.exterior.coords, strict=True)
    geodesic_area, _perimeter = geod.polygon_area_perimeter(longitudes, latitudes)
    # UTM on the central meridian is within a fraction of a percent of the ellipsoid.
    assert measured.value == pytest.approx(abs(geodesic_area), rel=0.002)


def test_projected_square_is_measured_in_the_file_crs():
    square = box(500000, 1400000, 501000, 1401000)
    measured = measure_geometry(square, CRS.from_epsg(32643))
    assert measured.calculated_in == "EPSG:32643"
    assert measured.value == pytest.approx(1_000_000, abs=0.001)


def test_hole_is_subtracted():
    polygon = Polygon(
        [(500000, 1400000), (501000, 1400000), (501000, 1401000), (500000, 1401000)],
        [
            [
                (500100, 1400100),
                (500300, 1400100),
                (500300, 1400300),
                (500100, 1400300),
                (500100, 1400100),
            ]
        ],
    )
    measured = measure_geometry(polygon, CRS.from_epsg(32643))
    assert measured.value == pytest.approx(960_000, abs=0.001)


def test_line_length_round_trips_through_wgs84():
    source = CRS.from_epsg(32643)
    line = LineString([(500000, 1400000), (503000, 1400000)])
    geographic = transform(Transformer.from_crs(source, 4326, always_xy=True).transform, line)
    measured = measure_geometry(geographic, CRS.from_epsg(4326))
    assert measured.type == "length"
    assert measured.unit == "meters"
    assert measured.calculated_in == "EPSG:32643"
    assert measured.value == pytest.approx(3000, abs=0.05)


def test_altitude_is_not_added_to_the_length():
    line = LineString([(500000, 1400000, 0), (501000, 1400000, 5000)])
    measured = measure_geometry(line, CRS.from_epsg(32643))
    assert measured.value == pytest.approx(1000, abs=0.01)
    assert "Altitude" in measured.note


def test_point_has_no_measurement():
    measured = measure_geometry(Point(77.5, 12.9), CRS.from_epsg(4326))
    assert measured.type == "none"
    assert measured.value is None
    assert "Points" in measured.note


def test_missing_crs_is_not_measured():
    measured = measure_geometry(box(0, 0, 1, 1), None)
    assert measured.value is None
    assert "No CRS" in measured.note


def test_same_degree_square_is_smaller_away_from_the_equator():
    def square(lon, lat):
        return Polygon(
            [
                (lon, lat),
                (lon + 1, lat),
                (lon + 1, lat + 1),
                (lon, lat + 1),
                (lon, lat),
            ]
        )

    equator = measure_geometry(square(0, 0), CRS.from_epsg(4326))
    north = measure_geometry(square(0, 60), CRS.from_epsg(4326))
    assert equator.calculated_in == "EPSG:32631"
    assert north.calculated_in == "EPSG:32631"
    assert equator.value > north.value * 1.5


def test_southern_polygon_uses_the_south_zone():
    polygon = Polygon(
        [(77.5, -12.0), (77.51, -12.0), (77.51, -12.01), (77.5, -12.01), (77.5, -12.0)]
    )
    measured = measure_geometry(polygon, CRS.from_epsg(4326))
    assert measured.calculated_in == "EPSG:32743"
    assert measured.value > 1000


def test_wide_polygon_uses_an_equal_area_projection():
    polygon = Polygon([(0, 0), (20, 0), (20, 5), (0, 5), (0, 0)])
    measured = measure_geometry(polygon, CRS.from_epsg(4326))
    assert measured.calculated_in.startswith("+proj=laea")
    assert measured.value > 1_000_000_000


def test_long_line_uses_an_equidistant_projection():
    measured = measure_geometry(LineString([(0, 0), (20, 0)]), CRS.from_epsg(4326))
    assert "+proj=aeqd" in measured.calculated_in
    assert measured.type == "length"
    assert measured.value > 2_000_000


def test_polar_polygon_is_not_forced_into_utm():
    polygon = Polygon([(10, 85.2), (10.2, 85.2), (10.2, 85.4), (10, 85.4), (10, 85.2)])
    measured = measure_geometry(polygon, CRS.from_epsg(4326))
    assert measured.value is not None
    assert "32661" in measured.calculated_in or "+proj=stere" in measured.calculated_in


def test_web_mercator_file_stays_in_web_mercator():
    measured = measure_geometry(box(0, 0, 1000, 1000), CRS.from_epsg(3857))
    assert measured.calculated_in == "EPSG:3857"
    assert measured.value == pytest.approx(1_000_000, abs=0.01)


def test_us_survey_feet_are_converted_to_metres():
    crs = CRS.from_epsg(2263)
    factor = crs.axis_info[0].unit_conversion_factor
    measured = measure_geometry(box(300000, 50000, 301000, 51000), crs)
    assert measured.unit == "square_meters"
    assert measured.value == pytest.approx(1_000_000 * factor * factor, rel=1e-6)


def test_invalid_polygon_does_not_raise():
    bowtie = Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)])
    measured = measure_geometry(bowtie, CRS.from_epsg(4326))
    assert measured.type == "area"
    assert measured.value is not None
    assert measured.value > 0


def test_empty_geometry_does_not_raise():
    measured = measure_geometry(Polygon(), CRS.from_epsg(4326))
    assert measured.type == "none"
    assert measured.value is None


def test_geometry_collection_keeps_area_and_length():
    geometry = GeometryCollection(
        [
            box(500000, 1400000, 501000, 1401000),
            LineString([(500000, 1400000), (500000, 1400500)]),
        ]
    )
    measured = measure_geometry(geometry, CRS.from_epsg(32643))
    assert measured.type == "mixed"
    assert measured.area_square_meters == pytest.approx(1_000_000, abs=0.1)
    assert measured.length_meters == pytest.approx(500, abs=0.1)


def test_unknown_geometry_does_not_raise():
    class CircularString:
        geom_type = "CircularString"
        is_empty = False
        has_z = False

    measured = measure_geometry(CircularString(), CRS.from_epsg(4326))
    assert measured.type == "none"
    assert measured.value is None
