import asyncio
from typing import BinaryIO
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import Settings


class Storage:
    """Thin async wrapper over boto3. boto3 is blocking, so calls run in a worker thread."""

    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self._client = self._make_client(settings, settings.s3_endpoint)
        # Signing needs no network access, but the URL embeds the host it was signed for —
        # so sign with the endpoint clients will actually reach.
        self._signer = self._make_client(
            settings, settings.s3_public_endpoint or settings.s3_endpoint
        )

    @staticmethod
    def _make_client(settings: Settings, endpoint: str | None):
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.upload_fileobj,
            fileobj,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        return await asyncio.to_thread(_get)

    async def delete_many(self, keys: list[str]) -> None:
        if not keys:
            return
        await asyncio.to_thread(
            self._client.delete_objects,
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
        )

    def presign_get(self, key: str, expires_in: int, download_name: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{quote(download_name)}"
            )
        return self._signer.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )

    async def ping(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)

    async def ensure_bucket(self) -> None:
        try:
            await self.ping()
        except ClientError:
            await asyncio.to_thread(self._client.create_bucket, Bucket=self.bucket)
