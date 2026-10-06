from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models import FeatureRow, UploadedFile


class MeasurementOut(BaseModel):
    type: Literal["area", "length", "none", "mixed"] = Field(
        description="area for polygons, length for lines, none for points and unsupported types."
    )
    value: float | None = Field(
        default=None,
        description=(
            "The measurement, rounded to 3 decimal places. Null when nothing was calculated."
        ),
    )
    unit: str | None = Field(
        default=None,
        description="square_meters or meters. Null for points and skipped features.",
    )
    calculated_in: str | None = Field(
        default=None,
        description=(
            "CRS used for the calculation. Geographic files are projected first, "
            "so this is often a UTM zone rather than the file CRS."
        ),
    )
    area_square_meters: float | None = None
    length_meters: float | None = None
    note: str | None = None


class FeatureOut(BaseModel):
    feature_id: str = Field(description="Zero-based index of the feature in the file.")
    geometry_type: str | None = None
    geometry: dict | None = Field(default=None, description="GeoJSON geometry.")
    crs: str | None = Field(default=None, description="CRS of the source file, when it has one.")
    properties: dict = Field(description="Attributes that came with the feature.")
    measurement: MeasurementOut


class FileOut(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: str = Field(description="PROCESSING, COMPLETED, or FAILED.")
    byte_size: int
    layer: str | None = None
    error: str | None = None
    notes: list[str] = []
    created_at: datetime


class FileList(BaseModel):
    files: list[FileOut]


class MeasurementSummary(BaseModel):
    total_area_square_meters: float
    total_length_meters: float
    measured_features: int
    unmeasured_features: int


class MeasurementPage(BaseModel):
    file_id: str
    filename: str
    crs: str | None
    status: str
    feature_count: int = Field(description="Features stored for the file, ignoring filters.")
    matched_features: int = Field(description="Features matching the current filter.")
    offset: int
    limit: int
    error: str | None = None
    notes: list[str] = []
    summary: MeasurementSummary
    features: list[FeatureOut]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def serialize_file(record: UploadedFile) -> FileOut:
    return FileOut(
        id=record.id,
        filename=record.filename,
        feature_count=record.feature_count,
        crs=record.crs,
        status=record.status,
        byte_size=record.byte_size,
        layer=record.layer_name,
        error=record.error,
        notes=list(record.notes or []),
        created_at=_as_utc(record.created_at),
    )


def serialize_feature(row: FeatureRow, *, include_geometry: bool = True) -> FeatureOut:
    return FeatureOut(
        feature_id=row.feature_id,
        geometry_type=row.geometry_type,
        geometry=row.geometry if include_geometry else None,
        crs=row.crs,
        properties=row.properties or {},
        measurement=MeasurementOut(
            type=row.measurement_type,
            value=row.measurement_value,
            unit=row.measurement_unit,
            calculated_in=row.calculated_in,
            area_square_meters=row.area_square_meters,
            length_meters=row.length_meters,
            note=row.measurement_note,
        ),
    )
