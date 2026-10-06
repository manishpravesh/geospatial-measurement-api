from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FileStatus:
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UploadedFile(Base):
    """One uploaded KML, KMZ, or zipped shapefile."""

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(Integer)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    crs: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=FileStatus.PROCESSING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    layer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeatureRow(Base):
    """A single geometry from an upload, plus the measurement we could make."""

    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        index=True,
    )
    feature_id: Mapped[str] = mapped_column(String(64))
    geometry_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    crs: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    properties: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    measurement_type: Mapped[str] = mapped_column(String(32))
    measurement_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    measurement_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    calculated_in: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    measurement_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    area_square_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_meters: Mapped[float | None] = mapped_column(Float, nullable=True)
