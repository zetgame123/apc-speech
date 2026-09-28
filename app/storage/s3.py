import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("s3_storage")


class S3Storage:
    def __init__(self):
        settings = get_settings()
        self.endpoint_url = settings.s3_endpoint_url
        self.bucket_audio = settings.s3_bucket_audio
        self.bucket_exports = settings.s3_bucket_exports
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_buckets(self):
        for bucket in [self.bucket_audio, self.bucket_exports]:
            try:
                self.s3_client.head_bucket(Bucket=bucket)
            except ClientError:
                try:
                    self.s3_client.create_bucket(Bucket=bucket)
                    logger.info("s3_bucket_created", bucket=bucket)
                except Exception as e:
                    logger.error("s3_bucket_creation_failed", bucket=bucket, error=str(e))

    def upload_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        self.s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    def download_bytes(self, bucket: str, key: str) -> bytes:
        response = self.s3_client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def download_to_file(self, bucket: str, key: str, local_path: str):
        self.s3_client.download_file(Bucket=bucket, Key=key, Filename=local_path)

    def upload_file(
        self,
        local_path: str,
        bucket: str,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        self.s3_client.upload_file(
            Filename=local_path,
            Bucket=bucket,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )
        return key

    def delete_object(self, bucket: str, key: str) -> bool:
        try:
            self.s3_client.delete_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            logger.error("s3_delete_error", bucket=bucket, key=key, error=str(e))
            return False

    def check_health(self) -> bool:
        try:
            self.s3_client.list_buckets()
            return True
        except Exception:
            return False


_storage_instance: S3Storage | None = None


def get_storage() -> S3Storage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = S3Storage()
    return _storage_instance
