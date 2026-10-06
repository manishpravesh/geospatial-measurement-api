import io
import zipfile
from pathlib import Path

import pytest

from app.exceptions import RejectedUpload
from app.services.archive import extract_kml_from_kmz, extract_shapefile


def zip_to(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def test_shapefile_sidecars_are_required(tmp_path):
    archive = zip_to(
        tmp_path / "parcel.zip",
        {"parcel.shp": b"shp", "parcel.shx": b"shx"},
    )
    with pytest.raises(RejectedUpload, match=r"\.dbf"):
        extract_shapefile(
            archive,
            tmp_path / "out",
            max_unzipped_bytes=1_000_000,
            max_members=20,
        )


def test_nested_shapefile_is_accepted(tmp_path):
    archive = zip_to(
        tmp_path / "survey.zip",
        {
            "survey/parcel.shp": b"shp",
            "survey/parcel.shx": b"shx",
            "survey/parcel.dbf": b"dbf",
            "survey/parcel.prj": b"prj",
            "__MACOSX/parcel.shp": b"junk",
            "notes.txt": b"ignore me",
        },
    )
    chosen = extract_shapefile(
        archive,
        tmp_path / "out",
        max_unzipped_bytes=1_000_000,
        max_members=20,
    )
    assert chosen.name == "parcel.shp"
    assert chosen.parent.name == "survey"
    assert not (tmp_path / "out" / "notes.txt").exists()


def test_two_shapefiles_are_rejected(tmp_path):
    archive = zip_to(
        tmp_path / "both.zip",
        {
            "a.shp": b"1",
            "a.shx": b"1",
            "a.dbf": b"1",
            "b.shp": b"2",
            "b.shx": b"2",
            "b.dbf": b"2",
        },
    )
    with pytest.raises(RejectedUpload, match="more than one shapefile"):
        extract_shapefile(
            archive,
            tmp_path / "out",
            max_unzipped_bytes=1_000_000,
            max_members=20,
        )


def test_zip_slip_is_rejected(tmp_path):
    archive = zip_to(
        tmp_path / "evil.zip",
        {"../secret.txt": b"owned", "parcel.shp": b"shp"},
    )
    with pytest.raises(RejectedUpload, match="escapes"):
        extract_shapefile(
            archive,
            tmp_path / "out",
            max_unzipped_bytes=1_000_000,
            max_members=20,
        )
    assert not (tmp_path / "secret.txt").exists()


def test_kmz_prefers_doc_kml(tmp_path):
    archive = zip_to(
        tmp_path / "sites.kmz",
        {"doc.kml": b"<kml></kml>", "other.kml": b"<kml></kml>", "icon.png": b"png"},
    )
    chosen = extract_kml_from_kmz(
        archive,
        tmp_path / "out",
        max_unzipped_bytes=1_000_000,
        max_members=20,
    )
    assert chosen.name == "doc.kml"
    assert chosen.read_bytes().startswith(b"<kml")


def test_bad_zip_is_rejected(tmp_path):
    archive = tmp_path / "nope.zip"
    archive.write_bytes(b"this is not a zip")
    with pytest.raises(RejectedUpload, match="not a valid zip"):
        extract_shapefile(
            archive,
            tmp_path / "out",
            max_unzipped_bytes=1_000_000,
            max_members=20,
        )


def test_declared_size_limit(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("parcel.shp", b"x" * 50)
        archive.writestr("parcel.shx", b"y" * 50)
        archive.writestr("parcel.dbf", b"z" * 50)
    archive = tmp_path / "big.zip"
    archive.write_bytes(buffer.getvalue())
    with pytest.raises(RejectedUpload, match="size limit"):
        extract_shapefile(archive, tmp_path / "out", max_unzipped_bytes=40, max_members=20)
