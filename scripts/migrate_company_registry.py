import argparse
import json
from datetime import datetime, timezone

from platforms import PLATFORMS, raw_candidates_file, status_file, validated_file


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


def migrate_platform(platform, dry_run=False):
    config = PLATFORMS[platform]
    legacy_file = config["company_file"]
    legacy_slugs = set(load_json(legacy_file, []))
    validated_path = validated_file(platform)
    status_path = status_file(platform)
    raw_path = raw_candidates_file(platform)

    existing_validated = set(load_json(validated_path, []))
    validated_set = legacy_slugs | existing_validated
    validated = sorted(validated_set)

    statuses = load_json(status_path, {})
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for slug in validated:
        current = statuses.get(slug, {})
        statuses[slug] = {
            "slug": slug,
            "platform": platform,
            "status": current.get("status", "empty"),
            "last_checked": current.get("last_checked"),
            "last_active": current.get("last_active"),
            "last_job_count": current.get("last_job_count", 0),
            "fail_count": current.get("fail_count", 0),
            "priority": current.get("priority", "daily"),
            "created_at": current.get("created_at", now),
            "updated_at": current.get("updated_at", now),
        }

    raw_candidates = sorted(set(load_json(raw_path, [])) | validated_set)

    if not dry_run:
        save_json(validated_path, validated)
        save_json(status_path, statuses)
        save_json(raw_path, raw_candidates)
        save_json(legacy_file, validated)

    return {
        "platform": platform,
        "validated": len(validated),
        "status": len(statuses),
        "raw_candidates": len(raw_candidates),
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy company lists into the registry.")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), action="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    platforms = args.platform or sorted(PLATFORMS)
    for platform in platforms:
        stats = migrate_platform(platform, dry_run=args.dry_run)
        print(
            f"{stats['platform']}: validated={stats['validated']:,}, "
            f"status={stats['status']:,}, raw={stats['raw_candidates']:,}"
        )


if __name__ == "__main__":
    main()
