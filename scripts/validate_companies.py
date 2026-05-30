import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import scraper
from platforms import PLATFORMS, raw_candidates_file, status_file, validated_file


FETCHERS = scraper.FETCHERS


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if "company_status" in path.parts:
            json.dump(data, f, separators=(",", ":"), sort_keys=isinstance(data, dict))
        else:
            json.dump(data, f, indent=2, sort_keys=isinstance(data, dict))
        f.write("\n")


def classify_result(jobs, status_code):
    if jobs:
        return "active", "daily"
    if status_code in (404, 410):
        return "dead", "paused"
    if status_code in (None, 429, 500, 502, 503, 504):
        return "flaky", "weekly"
    return "empty", "weekly"


def validate_slug(platform, slug):
    _, jobs, status_code = FETCHERS[platform](slug)
    status, priority = classify_result(jobs, status_code)
    return slug, jobs, status_code, status, priority


def validate_platform(platform, limit=None, workers=10, dry_run=False, new_only=False):
    raw = set(load_json(raw_candidates_file(platform), []))
    validated = set(load_json(validated_file(platform), []))
    statuses = load_json(status_file(platform), {})
    candidates = sorted(raw - set(statuses)) if new_only else sorted(raw | validated)
    if limit:
        candidates = candidates[:limit]

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    counts = {"active": 0, "empty": 0, "flaky": 0, "dead": 0}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(validate_slug, platform, slug): slug for slug in candidates}
        for i, future in enumerate(as_completed(futures), 1):
            slug, jobs, status_code, status, priority = future.result()
            counts[status] += 1
            record = statuses.get(slug, {
                "slug": slug,
                "platform": platform,
                "created_at": now,
                "fail_count": 0,
            })
            record.update(
                {
                    "slug": slug,
                    "platform": platform,
                    "status": status,
                    "last_checked": now,
                    "last_job_count": len(jobs),
                    "priority": priority,
                    "updated_at": now,
                }
            )
            if jobs:
                record["last_active"] = now
                record["fail_count"] = 0
                validated.add(slug)
            elif status == "dead":
                record["fail_count"] = record.get("fail_count", 0) + 1
                validated.discard(slug)
            elif status == "flaky":
                record["fail_count"] = record.get("fail_count", 0) + 1
            else:
                validated.add(slug)
            statuses[slug] = record

            if i % 25 == 0:
                print(f"{platform}: validated {i}/{len(candidates)} candidates")

    if not dry_run:
        save_json(status_file(platform), statuses)
        save_json(validated_file(platform), sorted(validated))
        save_json(PLATFORMS[platform]["company_file"], sorted(validated))

    print(
        f"{platform}: active={counts['active']:,}, empty={counts['empty']:,}, "
        f"flaky={counts['flaky']:,}, dead={counts['dead']:,}, validated={len(validated):,}"
    )
    return counts


def main():
    parser = argparse.ArgumentParser(description="Validate discovered company slugs.")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--new-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for platform in args.platform or sorted(PLATFORMS):
        validate_platform(
            platform,
            limit=args.limit,
            workers=args.workers,
            dry_run=args.dry_run,
            new_only=args.new_only,
        )


if __name__ == "__main__":
    main()
