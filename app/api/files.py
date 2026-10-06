import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import delete, func, select

from app.api.deps import AppSettings, DbSession
from app.exceptions import RejectedUpload
from app.models import FeatureRow, UploadedFile
from app.schemas import (
    FileList,
    FileOut,
    MeasurementPage,
    MeasurementSummary,
    serialize_feature,
    serialize_file,
)
from app.services.ingest import ingest_upload, remove_tree

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])
_FILE_ID = re.compile(r"^[0-9a-f]{32}$")


def _get_record(db: DbSession, file_id: str) -> UploadedFile:
    if _FILE_ID.fullmatch(file_id) is None:
        raise HTTPException(status_code=404, detail="File not found.")
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return record


@router.get("", response_model=FileList, include_in_schema=False)
@router.get("/", response_model=FileList, summary="List uploaded files")
def list_files(db: DbSession) -> FileList:
    rows = db.scalars(
        select(UploadedFile)
        .order_by(UploadedFile.created_at.desc(), UploadedFile.id.desc())
        .limit(500)
    ).all()
    return FileList(files=[serialize_file(row) for row in rows])


@router.post("", response_model=FileOut, status_code=201, include_in_schema=False)
@router.post(
    "/",
    response_model=FileOut,
    status_code=201,
    summary="Upload and measure a geospatial file",
)
def upload_file(
    db: DbSession,
    settings: AppSettings,
    file: UploadFile = File(
        ...,
        description="A .kml file, a .kmz archive, or a .zip containing one shapefile.",
    ),
    assume_crs: str | None = Form(
        default=None,
        description=(
            "CRS to assume when the file has none, for example EPSG:4326. "
            "Ignored when the file already carries a CRS."
        ),
    ),
) -> FileOut:
    """Store the upload and measure it before responding.

    A kept file returns 201. `status` is COMPLETED or FAILED. The file is
    rejected with nothing stored when the type, size, or archive layout is wrong.
    """
    try:
        record = ingest_upload(
            db,
            settings,
            filename=file.filename,
            stream=file.file,
            assume_crs=assume_crs,
        )
    except RejectedUpload as exc:
        logger.info("rejected upload: %s", exc)
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return serialize_file(record)


@router.get("/{file_id}", response_model=FileOut, include_in_schema=False)
@router.get("/{file_id}/", response_model=FileOut, summary="Get file information")
def get_file(file_id: str, db: DbSession) -> FileOut:
    return serialize_file(_get_record(db, file_id))


@router.get(
    "/{file_id}/measurements",
    response_model=MeasurementPage,
    include_in_schema=False,
)
@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementPage,
    summary="Get feature measurements",
)
def get_measurements(
    file_id: str,
    db: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    geometry_type: str | None = Query(
        default=None,
        description="Only return this geometry type. Matching is case-insensitive.",
    ),
    include_geometry: bool = Query(
        default=True,
        description="Set false to drop GeoJSON coordinates from the response.",
    ),
) -> MeasurementPage:
    record = _get_record(db, file_id)
    filters = [FeatureRow.file_id == record.id]
    if geometry_type and geometry_type.strip():
        filters.append(func.lower(FeatureRow.geometry_type) == geometry_type.strip().lower())

    matched = db.scalar(select(func.count()).select_from(FeatureRow).where(*filters)) or 0
    unmeasured = (
        db.scalar(
            select(func.count())
            .select_from(FeatureRow)
            .where(*filters, FeatureRow.measurement_type == "none")
        )
        or 0
    )
    area_total = db.scalar(
        select(func.coalesce(func.sum(FeatureRow.area_square_meters), 0.0)).where(*filters)
    )
    length_total = db.scalar(
        select(func.coalesce(func.sum(FeatureRow.length_meters), 0.0)).where(*filters)
    )
    rows = db.scalars(
        select(FeatureRow).where(*filters).order_by(FeatureRow.id).offset(offset).limit(limit)
    ).all()
    return MeasurementPage(
        file_id=record.id,
        filename=record.filename,
        crs=record.crs,
        status=record.status,
        feature_count=record.feature_count,
        matched_features=int(matched),
        offset=offset,
        limit=limit,
        error=record.error,
        notes=list(record.notes or []),
        summary=MeasurementSummary(
            total_area_square_meters=round(float(area_total or 0.0), 3),
            total_length_meters=round(float(length_total or 0.0), 3),
            measured_features=int(matched) - int(unmeasured),
            unmeasured_features=int(unmeasured),
        ),
        features=[serialize_feature(row, include_geometry=include_geometry) for row in rows],
    )


@router.delete("/{file_id}", status_code=204, include_in_schema=False)
@router.delete("/{file_id}/", status_code=204, summary="Delete an uploaded file")
def delete_file(file_id: str, db: DbSession, settings: AppSettings) -> Response:
    record = _get_record(db, file_id)
    folder = settings.upload_dir
    db.execute(delete(FeatureRow).where(FeatureRow.file_id == record.id))
    db.delete(record)
    db.commit()
    remove_tree(Path(folder) / file_id)
    return Response(status_code=204)
