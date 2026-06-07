import argparse
import gzip
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

from job_validator import MIN_PUBLISH_HEALTH_SCORE, should_publish


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
    if not verification.get("enabled"):
        raise RuntimeError(
            "Refusing to publish chunks without verification metadata. "
            "Run scripts/merge_data.py with URL validation enabled first."
        )
    if not verification.get("strict_mode"):
        raise RuntimeError("Refusing to publish: strict verification mode metadata is missing.")
    if verification.get("require_active") is not True:
        raise RuntimeError("Refusing to publish: manifest does not require active-only jobs.")
    if int(verification.get("min_health_score") or 0) < MIN_PUBLISH_HEALTH_SCORE:
        raise RuntimeError("Refusing to publish: health threshold is below strict minimum.")
    stats = verification.get("stats") or {}
    if not stats.get("published"):
        raise RuntimeError("Refusing to publish: verification produced zero publishable jobs.")
    if stats.get("dropped_unverified", 0) > 0 or stats.get("dropped_suspicious", 0) > 0:
        raise RuntimeError("Refusing to publish: validation left unverified or suspicious failures.")
    if stats.get("input", 0) != stats.get("cache_hits", 0) + stats.get("checked", 0):
        raise RuntimeError("Refusing to publish: validation did not account for every input job.")
    return metadata


def assert_chunks_strictly_verified(chunks_dir, manifest, min_health_score):
    total = 0
    for filename in manifest.get("chunks", []):
        chunk_path = chunks_dir / filename
        if not chunk_path.exists():
            raise FileNotFoundError(chunk_path)
        with gzip.open(chunk_path, "rt", encoding="utf-8") as f:
            jobs = json.load(f)
        for index, job in enumerate(jobs):
            total += 1
            if job.get("verification_status") != "active":
                raise RuntimeError(f"Refusing to publish: {filename}[{index}] is not verified active.")
            if int(job.get("job_health_score") or 0) < min_health_score:
                raise RuntimeError(f"Refusing to publish: {filename}[{index}] is below health threshold.")
            if not job.get("last_verified_at"):
                raise RuntimeError(f"Refusing to publish: {filename}[{index}] has no last_verified_at.")
            if not should_publish(job, min_health_score=min_health_score):
                raise RuntimeError(f"Refusing to publish: {filename}[{index}] failed strict publish check.")
    if total != manifest.get("totalJobs"):
        raise RuntimeError(
            f"Refusing to publish: manifest totalJobs={manifest.get('totalJobs')} but chunks contain {total}."
        )


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
    verification = manifest.get("verification") or {}
    if not verification.get("enabled") or not verification.get("strict_mode"):
        raise RuntimeError("Refusing to publish: manifest is not strict verified-only output.")
    min_health_score = int(verification.get("min_health_score") or MIN_PUBLISH_HEALTH_SCORE)
    assert_chunks_strictly_verified(chunks_dir, manifest, min_health_score)
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
