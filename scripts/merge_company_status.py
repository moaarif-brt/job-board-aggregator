import argparse
import json
from pathlib import Path

from platforms import PLATFORMS, status_file


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")


def merge_statuses(artifacts_dir):
    artifacts_root = Path(artifacts_dir)
    merged_counts = {}

    for platform in PLATFORMS:
        merged = load_json(status_file(platform), {})
        for path in sorted(artifacts_root.rglob(f"status_{platform}.json")):
            shard_status = load_json(path, {})
            for slug, incoming in shard_status.items():
                existing = merged.get(slug)
                if not existing:
                    merged[slug] = incoming
                    continue
                incoming_updated = incoming.get("updated_at") or ""
                existing_updated = existing.get("updated_at") or ""
                if incoming_updated >= existing_updated:
                    merged[slug] = incoming
        save_json(status_file(platform), merged)
        merged_counts[platform] = len(merged)
        print(f"{platform}: {len(merged):,} status records")

    return merged_counts


def main():
    parser = argparse.ArgumentParser(description="Merge company status files from shard artifacts.")
    parser.add_argument("--artifacts-dir", default="scripts/artifacts")
    args = parser.parse_args()
    merge_statuses(args.artifacts_dir)


if __name__ == "__main__":
    main()
