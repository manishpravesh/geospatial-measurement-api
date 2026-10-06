import json
import logging
import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from shapely.geometry import mapping
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import FileContentError, RejectedUpload
from app.models import FeatureRow, FileStatus, UploadedFile
from app.services.archive import extract_kml_from_kmz, extract_shapefile
from app.services.crs import CRSError, crs_label, parse_crs
from app.services.datasets import LoadedDataset, LoadedFeature
from app.services.kml import read_kml
from app.services.measure import Measurement, measure_geometry
from app.services.shapefile import read_shapefile

logger = logging.getLogger(__name__)

ACCEPTED_SUFFIXES = {".kml", ".kmz", ".zip"}
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class PreparedInput:
    def __init__(self, kind: str, path: Path):
        self.kind = kind
        self.path = path


def display_filename(name: str | None) -> str:
    raw = (name or "upload").replace("\x00", "")
    raw = Path(raw).name.strip()
    if raw in {"", ".", ".."}:
        return "upload"
    return raw[:255]


def safe_filename(name: str, suffix: str) -> str:
    cleaned = _UNSAFE_NAME.sub("_", Path(name).name).strip("._")
    stem = Path(cleaned).stem.strip("._") or "upload"
    return f"{stem[:120]}{suffix}"


def limit_message(limit: int) -> str:
    megabyte = 1024 * 1024
    if limit >= megabyte and limit % megabyte == 0:
        return f"File is larger than {limit // megabyte} MB."
    return f"File is larger than {limit} bytes."


def write_capped(stream, dest: Path, limit: int) -> int:
    total = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise RejectedUpload(limit_message(limit), status_code=413)
            handle.write(chunk)
    if total == 0:
        raise RejectedUpload("The uploaded file is empty.")
    return total


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if attempt == 2:
                logger.warning("could not remove %s", path, exc_info=True)
                return
            time.sleep(0.05)


def prepare_input(
    source: Path, suffix: str, extract_dir: Path, settings: Settings
) -> PreparedInput:
    if suffix == ".kml":
        return PreparedInput("kml", source)
    extract_dir.mkdir(parents=True, exist_ok=True)
    limits = {
        "max_unzipped_bytes": settings.max_unzipped_bytes,
        "max_members": settings.max_zip_members,
    }
    if suffix == ".kmz":
        return PreparedInput("kml", extract_kml_from_kmz(source, extract_dir, **limits))
    if suffix == ".zip":
        return PreparedInput("shapefile", extract_shapefile(source, extract_dir, **limits))
    raise RejectedUpload("Upload a .kml file, a .kmz archive, or a .zip containing one shapefile.")


def load_dataset(prepared: PreparedInput) -> LoadedDataset:
    if prepared.kind == "kml":
        return read_kml(prepared.path)
    if prepared.kind == "shapefile":
        return read_shapefile(prepared.path)
    raise FileContentError("Unsupported file type.")


def _dedupe(notes: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for note in notes:
        if note and note not in seen:
            seen.add(note)
            ordered.append(note)
    return ordered


def apply_crs_override(dataset: LoadedDataset, assume_crs: str | None) -> None:
    if not assume_crs:
        return
    supplied = parse_crs(assume_crs)
    supplied_label = crs_label(supplied) or assume_crs
    if dataset.crs is None:
        dataset.crs = supplied
        dataset.notes = [note for note in dataset.notes if "Measurements were skipped" not in note]
        dataset.notes.append(
            f"The file did not include a CRS. Measurements used {supplied_label} from assume_crs."
        )
        return
    try:
        same = bool(dataset.crs.equals(supplied))
    except Exception:
        same = crs_label(dataset.crs) == supplied_label
    if not same:
        file_label = crs_label(dataset.crs) or "the file CRS"
        dataset.notes.append(
            f"assume_crs={supplied_label} was ignored because the file CRS is {file_label}."
        )


def add_projection_note(dataset: LoadedDataset) -> None:
    if dataset.crs is not None and dataset.crs.is_geographic:
        dataset.notes.append(
            "Area and length are calculated in a projected CRS chosen per feature. "
            "Longitude and latitude are not treated as metres."
        )


def _geometry_json(geometry):
    if geometry is None:
        return None
    return json.loads(json.dumps(mapping(geometry)))


def _join_notes(*parts: str | None) -> str | None:
    cleaned = [part for part in parts if part]
    if not cleaned:
        return None
    return " ".join(cleaned)


def build_row(
    file_id: str,
    index: int,
    feature: LoadedFeature,
    crs_text: str | None,
    measurement: Measurement,
) -> FeatureRow:
    geometry_type = feature.geometry.geom_type if feature.geometry is not None else None
    try:
        geometry = _geometry_json(feature.geometry)
    except Exception:
        geometry = None
        measurement = Measurement(type="none", note="Feature geometry could not be stored.")
    try:
        properties = json.loads(json.dumps(feature.properties or {}))
    except TypeError:
        properties = {}
    return FeatureRow(
        file_id=file_id,
        feature_id=str(index),
        geometry_type=geometry_type,
        geometry=geometry,
        crs=crs_text,
        properties=properties,
        measurement_type=measurement.type,
        measurement_value=measurement.value,
        measurement_unit=measurement.unit,
        calculated_in=measurement.calculated_in,
        measurement_note=_join_notes(feature.warning, measurement.note),
        area_square_meters=measurement.area_square_meters,
        length_meters=measurement.length_meters,
    )


def fallback_row(file_id: str, index: int, crs_text: str | None) -> FeatureRow:
    return FeatureRow(
        file_id=file_id,
        feature_id=str(index),
        geometry_type=None,
        geometry=None,
        crs=crs_text,
        properties={},
        measurement_type="none",
        measurement_value=None,
        measurement_unit=None,
        calculated_in=None,
        measurement_note="This feature could not be measured.",
        area_square_meters=None,
        length_meters=None,
    )


def mark_failed(db: Session, file_id: str, message: str) -> UploadedFile:
    db.rollback()
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise RuntimeError(f"Upload {file_id} disappeared during processing.")
    record.status = FileStatus.FAILED
    record.error = message[:1000]
    record.feature_count = 0
    db.commit()
    logger.info("processed %s status=%s error=%s", record.id, record.status, record.error)
    return record


def _clean_assume_crs(assume_crs: str | None) -> str | None:
    if assume_crs is None:
        return None
    cleaned = assume_crs.strip()
    if not cleaned:
        return None
    try:
        parse_crs(cleaned)
    except CRSError as exc:
        raise RejectedUpload(str(exc)) from exc
    return cleaned


def ingest_upload(
    db: Session,
    settings: Settings,
    *,
    filename: str | None,
    stream,
    assume_crs: str | None,
) -> UploadedFile:
    assume_crs = _clean_assume_crs(assume_crs)
    try:
        stream.seek(0)
    except Exception:
        pass
    display_name = display_filename(filename)
    suffix = Path(display_name).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise RejectedUpload(
            "Upload a .kml file, a .kmz archive, or a .zip containing one shapefile."
        )

    file_id = uuid.uuid4().hex
    root = Path(settings.upload_dir) / file_id
    source_path = root / "source" / safe_filename(display_name, suffix)
    record: UploadedFile | None = None
    try:
        size = write_capped(stream, source_path, settings.max_upload_bytes)
        prepared = prepare_input(source_path, suffix, root / "extracted", settings)
        record = UploadedFile(
            id=file_id,
            filename=display_name,
            stored_path=str(source_path),
            byte_size=size,
            feature_count=0,
            crs=None,
            status=FileStatus.PROCESSING,
            error=None,
            notes=[],
            layer_name=None,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        db.commit()
    except RejectedUpload:
        remove_tree(root)
        raise
    except Exception:
        remove_tree(root)
        raise

    try:
        dataset = load_dataset(prepared)
        apply_crs_override(dataset, assume_crs)
        add_projection_note(dataset)
        if len(dataset.features) > settings.max_features:
            raise FileContentError(
                f"This file has {len(dataset.features)} features. "
                f"The limit is {settings.max_features}."
            )
        crs_text = crs_label(dataset.crs) if dataset.crs is not None else None
        rows: list[FeatureRow] = []
        for index, feature in enumerate(dataset.features):
            try:
                measured = measure_geometry(feature.geometry, dataset.crs)
                rows.append(build_row(file_id, index, feature, crs_text, measured))
            except Exception:
                logger.exception("feature %s on %s could not be measured", index, file_id)
                rows.append(fallback_row(file_id, index, crs_text))
    except FileContentError as exc:
        return mark_failed(db, file_id, str(exc))
    except Exception:
        logger.exception("failed to process %s", file_id)
        return mark_failed(db, file_id, "Could not process this file.")

    stored = db.get(UploadedFile, file_id)
    if stored is None:
        raise RuntimeError(f"Upload {file_id} disappeared during processing.")
    try:
        stored.feature_count = len(rows)
        stored.crs = crs_text
        stored.notes = _dedupe(dataset.notes)
        stored.layer_name = dataset.layer_name
        stored.status = FileStatus.COMPLETED
        stored.error = None
        db.add_all(rows)
        db.commit()
    except Exception:
        logger.exception("failed to save measurements for %s", file_id)
        return mark_failed(db, file_id, "Could not process this file.")
    logger.info(
        "processed %s filename=%s status=%s features=%s",
        stored.id,
        stored.filename,
        stored.status,
        stored.feature_count,
    )
    return stored
