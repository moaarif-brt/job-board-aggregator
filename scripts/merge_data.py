import argparse
import json
import gzip
import os
import re
from pathlib import Path
from datetime import datetime, timezone

from job_validator import (
    MIN_PUBLISH_HEALTH_SCORE,
    ACTIVE,
    is_stale,
    should_publish,
    validate_jobs,
)

CHUNK_SIZE = 25_000

def get_dedup_key(job):
    url = job.get("url", "")
    ats = job.get("ats")
    if ats == "Workday":
        # Extract numeric job ID from URL, fall back to url if not found
        match = re.search(r'/jobs/(\d+)', url)
        if match:
            company = job.get("company", "")
            return f"workday:{company}:{match.group(1)}"
    if ats == "SmartRecruiters":
        match = re.search(r"/(\d{9,})(?:/|$)", url)
        if match:
            company = job.get("company", "")
            return f"smartrecruiters:{company}:{match.group(1)}"
    return url


def load_chunks(directory):
    """Load all jobs from chunked gzip files via manifest."""
    chunks_dir = Path(directory) / "chunks"
    manifest_path = chunks_dir / "jobs_manifest.json"
    if not manifest_path.exists():
        return []
    with open(manifest_path) as f:
        manifest = json.load(f)
    jobs = []
    for chunk_file in manifest["chunks"]:
        chunk_path = chunks_dir / chunk_file
        if chunk_path.exists():
            with gzip.open(chunk_path, "rt", encoding="utf-8") as f:
                jobs.extend(json.load(f))
    return jobs


def load_jobs_file(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_artifact_jobs(artifacts_dir):
    """Load jobs.json files from all scraper shard artifacts."""
    root = Path(artifacts_dir)
    if not root.exists():
        return []

    jobs = []
    for path in sorted(root.rglob("jobs.json")):
        shard_jobs = load_jobs_file(path)
        print(f"Loaded {len(shard_jobs):,} jobs from {path}")
        jobs.extend(shard_jobs)
    return jobs


def save_chunks(jobs, directory, timestamp, verification=None):
    """Write chunked gzip files + manifest."""
    chunks_dir = Path(directory) / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean old chunks
    for f in os.listdir(chunks_dir):
        if f.startswith("jobs_chunk_") and f.endswith(".json.gz"):
            os.remove(chunks_dir / f)
    
    # Sort consistently
    jobs.sort(key=lambda x: (x.get('company', '').lower(), x.get('title', '').lower()))
    chunks = [jobs[i:i + CHUNK_SIZE] for i in range(0, len(jobs), CHUNK_SIZE)]
    
    chunk_filenames = []
    for idx, chunk in enumerate(chunks):
        chunk_file = f"jobs_chunk_{idx}.json.gz"
        with gzip.open(chunks_dir / chunk_file, "wt", encoding="utf-8") as f:
            json.dump(chunk, f, indent=0)
        chunk_filenames.append(chunk_file)
        print(f"  Chunk {idx}: {len(chunk):,} jobs")
    
    manifest = {
        "chunks": chunk_filenames,
        "totalJobs": len(jobs),
        "last_updated": timestamp,
    }
    if verification:
        manifest["verification"] = verification
    with open(chunks_dir / "jobs_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)


def load_metadata_candidates(paths):
    for path in paths:
        path = Path(path)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def merge_job_data(
    new_jobs_dir="scripts/output",
    existing_data_dir="data",
    output_dir="data",
    artifacts_dir=None,
    validation_workers=32,
    min_health_score=MIN_PUBLISH_HEALTH_SCORE,
    allow_unverified=False,
):
    """Merge new scrape with existing data, removing stale jobs."""
    if allow_unverified:
        raise ValueError("--allow-unverified is disabled in strict verified-jobs-only mode")

    if artifacts_dir:
        new_jobs = load_artifact_jobs(artifacts_dir)
    else:
        new_jobs = load_chunks(new_jobs_dir)
    print(f"New scrape: {len(new_jobs):,} jobs")
    
    existing_jobs = load_chunks(existing_data_dir)
    print(f"Existing data: {len(existing_jobs):,} jobs")
    
    # Merge by URL. Existing jobs are only carried forward when they are still
    # fresh for their ATS or have a recent active verification record. This is
    # intentionally much stricter than the old global 30-day stale window.
    merged = {}
    stale_count = 0
    dead_existing_count = 0
    low_health_existing_count = 0
    for job in existing_jobs:
        key = get_dedup_key(job)
        if not key:
            continue
        if job.get("verification_status") == "dead":
            dead_existing_count += 1
            continue
        if job.get("verification_status") == ACTIVE and should_publish(job):
            merged[key] = job
            continue
        if is_stale(job):
            stale_count += 1
            continue
        if job.get("job_health_score") is not None and not should_publish(job):
            low_health_existing_count += 1
            continue
        merged[key] = job
    
    if stale_count > 0:
        print(f"Dropped {stale_count:,} stale existing jobs using ATS-specific windows")
    if dead_existing_count > 0:
        print(f"Dropped {dead_existing_count:,} previously verified dead jobs")
    if low_health_existing_count > 0:
        print(f"Dropped {low_health_existing_count:,} low-health existing jobs")
    
    # New scrape always wins on duplicates
    for job in new_jobs:
        key = get_dedup_key(job)
        if key:
            merged[key] = job
    
    final_jobs = list(merged.values())
    print(f"Merged pre-validation result: {len(final_jobs):,} jobs")
    
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cache_path = Path(output_dir) / "job_validation_cache.json"
    final_jobs, validation_stats = validate_jobs(
        final_jobs,
        cache_path=cache_path,
        workers=validation_workers,
        min_health_score=min_health_score,
        require_active=not allow_unverified,
    )
    verification_metadata = {
        "enabled": True,
        "strict_mode": True,
        "min_health_score": min_health_score,
        "require_active": True,
        "stats": validation_stats,
    }
    print(
        "Validation result: "
        f"published={validation_stats['published']:,}, "
        f"dead={validation_stats['dropped_dead']:,}, "
        f"suspicious={validation_stats['dropped_suspicious']:,}, "
        f"unverified={validation_stats['dropped_unverified']:,}, "
        f"low_health={validation_stats['dropped_low_health']:,}"
    )
    save_chunks(final_jobs, output_dir, timestamp, verification=verification_metadata)
    
    # Update metadata
    metadata = load_metadata_candidates(
        [
            Path(new_jobs_dir) / "metadata.json",
            Path(output_dir) / "metadata.json",
        ]
    )
    metadata["total_jobs"] = len(final_jobs)
    metadata["last_updated"] = timestamp
    metadata["verification"] = verification_metadata
    with open(Path(output_dir) / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    
    print("Merge complete")
    return len(final_jobs)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge scraper output into chunked job data.")
    parser.add_argument("--new-jobs-dir", default="scripts/output")
    parser.add_argument("--existing-data-dir", default="data")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--artifacts-dir")
    parser.add_argument("--validation-workers", type=int, default=32)
    parser.add_argument("--min-health-score", type=int, default=MIN_PUBLISH_HEALTH_SCORE)
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Disabled in strict mode. Kept only to fail loudly for old automation.",
    )
    args = parser.parse_args()
    merge_job_data(
        new_jobs_dir=args.new_jobs_dir,
        existing_data_dir=args.existing_data_dir,
        output_dir=args.output_dir,
        artifacts_dir=args.artifacts_dir,
        validation_workers=args.validation_workers,
        min_health_score=args.min_health_score,
        allow_unverified=args.allow_unverified,
    )
