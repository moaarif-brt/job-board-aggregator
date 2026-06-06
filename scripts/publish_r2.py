import argparse
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config


ROOT_DIR = Path(__file__).resolve().parent.parent


def env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def upload_file(client, bucket, local_path, key):
    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    extra_args = {"ContentType": content_type}
    if local_path.suffix == ".gz":
        extra_args["ContentType"] = "application/gzip"

    client.upload_file(str(local_path), bucket, key, ExtraArgs=extra_args)
    print(f"Uploaded {local_path} -> s3://{bucket}/{key}")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def assert_verified_metadata(metadata_path):
    metadata = load_json(metadata_path) if metadata_path.exists() else {}
    verification = metadata.get("verification") or {}
    if os.environ.get("ALLOW_UNVERIFIED_PUBLISH") == "1":
        print("WARNING: ALLOW_UNVERIFIED_PUBLISH=1 set; skipping verification metadata guard")
        return metadata
    if not verification.get("enabled"):
        raise RuntimeError(
            "Refusing to publish chunks without verification metadata. "
            "Run scripts/merge_data.py with URL validation enabled first."
        )
    stats = verification.get("stats") or {}
    if not stats.get("published"):
        raise RuntimeError("Refusing to publish: verification produced zero publishable jobs.")
    return metadata


def publish(chunks_dir, prefix, metadata_path):
    account_id = env("R2_ACCOUNT_ID")
    bucket = env("R2_BUCKET")
    public_base_url = env("R2_PUBLIC_BASE_URL").rstrip("/")

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    metadata = assert_verified_metadata(metadata_path)
    manifest_path = chunks_dir / "jobs_manifest.json"
    manifest = load_json(manifest_path)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    latest_prefix = f"{prefix}/latest"
    snapshot_prefix = f"{prefix}/snapshots/{timestamp}"

    files = [manifest_path] + [chunks_dir / filename for filename in manifest.get("chunks", [])]
    for local_path in files:
        if not local_path.exists():
            raise FileNotFoundError(local_path)
        upload_file(client, bucket, local_path, f"{latest_prefix}/{local_path.name}")
        upload_file(client, bucket, local_path, f"{snapshot_prefix}/{local_path.name}")

    latest_manifest_url = f"{public_base_url}/{latest_prefix}/jobs_manifest.json"
    snapshot_manifest_url = f"{public_base_url}/{snapshot_prefix}/jobs_manifest.json"

    metadata.update(
        {
            "r2_manifest_url": latest_manifest_url,
            "r2_snapshot_manifest_url": snapshot_manifest_url,
            "r2_jobs_base_url": f"{public_base_url}/{latest_prefix}",
            "storage": "cloudflare_r2",
            "total_jobs": manifest.get("totalJobs", metadata.get("total_jobs", 0)),
            "last_updated": manifest.get("last_updated", metadata.get("last_updated")),
        }
    )
    save_json(metadata_path, metadata)

    config_path = ROOT_DIR / "data" / "config.json"
    config = load_json(config_path) if config_path.exists() else {}
    config["jobsBaseUrl"] = f"{public_base_url}/{latest_prefix}"
    config.setdefault("localJobsBaseUrl", "./data/chunks")
    save_json(config_path, config)

    print(f"R2 latest manifest: {latest_manifest_url}")
    return latest_manifest_url


def main():
    parser = argparse.ArgumentParser(description="Publish generated job chunks to Cloudflare R2.")
    parser.add_argument("--chunks-dir", default=str(ROOT_DIR / "data" / "chunks"))
    parser.add_argument("--prefix", default="jobs")
    parser.add_argument("--metadata", default=str(ROOT_DIR / "data" / "metadata.json"))
    args = parser.parse_args()

    publish(Path(args.chunks_dir), args.prefix.strip("/"), Path(args.metadata))


if __name__ == "__main__":
    main()
