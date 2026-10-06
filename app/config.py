from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Environment variables override these defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/app.db"
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 50 * 1024 * 1024
    max_unzipped_bytes: int = 200 * 1024 * 1024
    max_zip_members: int = 2000
    max_features: int = 20_000
