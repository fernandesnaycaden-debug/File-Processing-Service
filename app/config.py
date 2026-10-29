from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://files:files@localhost:5432/files"
    redis_url: str = "redis://localhost:6379/0"

    # S3 / MinIO. Leave s3_endpoint unset to talk to real AWS S3.
    s3_endpoint: str | None = "http://localhost:9000"
    # Host used when signing URLs handed to clients. Inside Docker the API reaches MinIO at
    # http://minio:9000, but a browser needs http://localhost:9000.
    s3_public_endpoint: str | None = None
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_bucket: str = "files"

    admin_token: str = "change-me"

    max_upload_bytes: int = 20 * 1024 * 1024
    thumbnail_sizes: list[int] = [128, 512]
    max_image_pixels: int = 50_000_000
    pdf_max_pages: int = 500

    signed_url_default_ttl: int = 300
    signed_url_max_ttl: int = 3600

    scanner: Literal["noop", "clamav"] = "noop"
    clamav_host: str = "localhost"
    clamav_port: int = 3310
    clamav_timeout: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
