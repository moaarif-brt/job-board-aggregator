import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from platforms import PLATFORMS, raw_candidates_file, status_file, validated_file


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
COMPANY_FILES = {name: config["company_file"] for name, config in PLATFORMS.items()}


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jobs(chunks_dir):
    manifest_path = chunks_dir / "jobs_manifest.json"
    manifest = load_json(manifest_path, {})
    jobs = []

    for filename in manifest.get("chunks", []):
        chunk_path = chunks_dir / filename
        if not chunk_path.exists():
            continue
        with gzip.open(chunk_path, "rt", encoding="utf-8") as f:
            jobs.extend(json.load(f))

    return manifest, jobs


def print_table(rows, headers):
    widths = [
        max(len(str(row[i])) for row in [headers, *rows])
        for i in range(len(headers))
    ]
    header_line = "  ".join(str(value).ljust(widths[i]) for i, value in enumerate(headers))
    print(header_line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)))


def main():
    parser = argparse.ArgumentParser(description="Audit job source coverage.")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top companies to show by current job count.",
    )
    args = parser.parse_args()

    metadata = load_json(DATA_DIR / "metadata.json", {})
    manifest, jobs = load_jobs(DATA_DIR / "chunks")

    print("\nSOURCE COVERAGE AUDIT")
    print("=" * 80)
    print(f"Metadata total jobs:   {metadata.get('total_jobs', 'unknown')}")
    print(f"Manifest total jobs:   {manifest.get('totalJobs', 'unknown')}")
    print(f"Loaded chunk jobs:     {len(jobs):,}")
    print(f"Last updated:          {manifest.get('last_updated') or metadata.get('last_updated') or 'unknown'}")
    if metadata.get("r2_manifest_url"):
        print(f"R2 manifest:           {metadata['r2_manifest_url']}")

    company_rows = []
    total_company_slugs = 0
    for platform, path in COMPANY_FILES.items():
        slugs = load_json(path, [])
        raw = load_json(raw_candidates_file(platform), [])
        validated = load_json(validated_file(platform), [])
        statuses = load_json(status_file(platform), {})
        status_counts = Counter(record.get("status", "unknown") for record in statuses.values())
        count = len(slugs)
        total_company_slugs += count
        company_rows.append(
            (
                platform,
                count,
                len(raw),
                len(validated),
                status_counts.get("active", 0),
                status_counts.get("empty", 0),
                status_counts.get("flaky", 0),
                status_counts.get("dead", 0),
            )
        )

    print("\nCompany source lists")
    print_table(company_rows, ("platform", "legacy", "raw", "validated", "active", "empty", "flaky", "dead"))
    print(f"\nTotal source slugs: {total_company_slugs:,}")

    if not jobs:
        return

    ats_counts = Counter(job.get("ats") or "Unknown" for job in jobs)
    active_by_ats = {}
    for ats in ats_counts:
        active_by_ats[ats] = len(
            {
                (job.get("company_slug") or job.get("company") or "").lower()
                for job in jobs
                if (job.get("ats") or "Unknown") == ats
            }
        )

    rows = [
        (ats, f"{count:,}", f"{active_by_ats.get(ats, 0):,}")
        for ats, count in ats_counts.most_common()
    ]
    print("\nJobs by ATS")
    print_table(rows, ("ats", "jobs", "active companies"))

    company_counts = Counter(
        (job.get("company") or job.get("company_slug") or "Unknown").lower()
        for job in jobs
    )
    print(f"\nTop {args.top} companies by jobs")
    top_rows = [(company, f"{count:,}") for company, count in company_counts.most_common(args.top)]
    print_table(top_rows, ("company", "jobs"))


if __name__ == "__main__":
    main()
