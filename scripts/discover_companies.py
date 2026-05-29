import argparse
import urllib.parse
import urllib.request
import json
import re
import os
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT_DIR, "data")

# Paths to ATS company lists
PLATFORM_FILES = {
    "greenhouse": os.path.join(DATA_DIR, "greenhouse_companies.json"),
    "lever": os.path.join(DATA_DIR, "lever_companies.json"),
    "ashby": os.path.join(DATA_DIR, "ashby_companies.json"),
    "bamboohr": os.path.join(DATA_DIR, "bamboohr_companies.json"),
    "workable": os.path.join(DATA_DIR, "workable_companies.json"),
    "recruitee": os.path.join(DATA_DIR, "recruitee_companies.json"),
    "personio": os.path.join(DATA_DIR, "personio_companies.json"),
    "smartrecruiters": os.path.join(DATA_DIR, "smartrecruiters_companies.json"),
}

COMMON_CRAWL_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
DEFAULT_COMMON_CRAWL_PLATFORMS = ("workable", "recruitee", "personio", "smartrecruiters")

COMMON_CRAWL_QUERIES = {
    "workable": ["apply.workable.com/*"],
    "recruitee": ["*.recruitee.com/*"],
    "personio": ["*.jobs.personio.de/*", "*.jobs.personio.com/*"],
    "smartrecruiters": ["jobs.smartrecruiters.com/*"],
}

# Subdomains to ignore when extracting company names from domain prefixes
BLACKLIST_SLUGS = {
    "www", "api", "support", "careers", "static", "assets", "blog", "help", "app", 
    "dev", "test", "demo", "jobs", "status", "security", "privacy", "legal", "terms",
    "docs", "dashboard", "portal", "admin", "mail", "web", "cdn", "download", "login",
    "go", "jobs-feed", "job", "j", "o", "xml", "api", "www2"
}

COMMON_CRAWL_SKIP_PATH_PARTS = {
    "api",
    "embed",
    "jobs",
    "job",
    "j",
    "o",
    "login",
    "account",
    "accounts",
    "widget",
}

# Regex patterns to detect ATS slugs in HTML source
PATTERNS = {
    "greenhouse": [
        r'boards\.greenhouse\.io/([^/\'"#?&]+)',
        r'boards\.greenhouse\.io/embed\?nodeId=([^/\'"#?&]+)'
    ],
    "lever": [
        r'jobs\.lever\.co/([^/\'"#?&]+)'
    ],
    "ashby": [
        r'jobs\.ashbyhq\.com/([^/\'"#?&]+)'
    ],
    "bamboohr": [
        r'([^/\."\'#?&\s]+)\.bamboohr\.com/careers',
        r'([^/\."\'#?&\s]+)\.bamboohr\.com/jobs'
    ],
    "workable": [
        r'apply\.workable\.com/([^/\'"#?&\s]+)'
    ],
    "recruitee": [
        r'([^/\."\'#?&\s]+)\.recruitee\.com'
    ],
    "personio": [
        r'([^/\."\'#?&\s]+)\.jobs\.personio\.(?:de|com)'
    ],
    "smartrecruiters": [
        r'(?:jobs|www)\.smartrecruiters\.com/([^/\'"#?&\s]+)',
        r'api\.smartrecruiters\.com/v1/companies/([^/\'"#?&\s]+)/postings'
    ]
}

# Create an unverified SSL context to bypass invalid HTTPS certificates of startups
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

def fetch_json_urllib(url, max_retries=3, timeout=15):
    """Fetch JSON data from a URL using urllib with retry logic."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch {url} after {max_retries} attempts: {e}")
            else:
                time.sleep(2)
    return None

def fetch_text_urllib(url, max_retries=3, timeout=30):
    """Fetch plain text content from a URL using urllib with retry logic."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
            )
            with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch {url} after {max_retries} attempts: {e}")
            else:
                time.sleep(2)
    return ""

def fetch_homepage_html(url):
    """Fetch website HTML content using urllib. Handles redirects and ignores SSL verification."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5"
            }
        )
        with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=8) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception:
        # Silently fail for individual website connectivity issues (extremely common)
        pass
    return ""

def load_existing_companies():
    """Load existing company lists to prevent checking or adding duplicates."""
    companies = {}
    for platform, filepath in PLATFORM_FILES.items():
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    companies[platform] = set(json.load(f))
            except Exception:
                companies[platform] = set()
        else:
            companies[platform] = set()
    return companies

def save_companies(platform, slugs):
    """Save the updated slugs list back to JSON."""
    filepath = PLATFORM_FILES[platform]
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(sorted(list(slugs)), f, indent=2)

def is_valid_slug(slug):
    slug = slug.lower().strip()
    if not slug or slug in BLACKLIST_SLUGS or len(slug) <= 1:
        return False
    if slug in COMMON_CRAWL_SKIP_PATH_PARTS:
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,80}", slug))

def extract_slugs_from_text(text):
    """Scan text content for ATS patterns and return found platform slugs."""
    found = {}
    for platform, regex_list in PATTERNS.items():
        for regex in regex_list:
            matches = re.findall(regex, text, re.IGNORECASE)
            for match in matches:
                slug = match.lower().strip()
                if is_valid_slug(slug):
                    if platform not in found:
                        found[platform] = set()
                    found[platform].add(slug)
    return found

def extract_slugs_from_html(html):
    """Scan HTML content for ATS patterns and return a dictionary of found platform slugs."""
    return extract_slugs_from_text(html)

def get_common_crawl_indexes(count):
    """Return newest Common Crawl index metadata."""
    collections = fetch_json_urllib(COMMON_CRAWL_COLLINFO_URL, timeout=30)
    if not isinstance(collections, list):
        print("Could not retrieve Common Crawl collection list.")
        return []

    indexes = []
    for collection in collections:
        index_id = collection.get("id")
        cdx_api = collection.get("cdx-api")
        if index_id and cdx_api:
            indexes.append({"id": index_id, "cdx_api": cdx_api})

    return indexes[:count]

def build_cdx_url(cdx_api, url_pattern, limit):
    params = {
        "url": url_pattern,
        "output": "json",
        "fl": "url",
        "filter": "status:200",
        "collapse": "urlkey",
        "limit": str(limit),
    }
    separator = "&" if "?" in cdx_api else "?"
    return f"{cdx_api}{separator}{urllib.parse.urlencode(params)}"

def discover_common_crawl(platforms, existing_slugs, index_count=2, limit=10000):
    """Discover ATS slugs by querying recent Common Crawl URL indexes."""
    indexes = get_common_crawl_indexes(index_count)
    discoveries = {platform: set() for platform in platforms}

    if not indexes:
        return discoveries

    print("\n" + "=" * 80)
    print("COMMON CRAWL ATS DISCOVERY")
    print("=" * 80)
    print(f"Indexes: {', '.join(index['id'] for index in indexes)}")
    print(f"Platforms: {', '.join(platforms)}")
    print(f"Limit per query: {limit:,}")
    print("-" * 80)

    for index in indexes:
        for platform in platforms:
            for url_pattern in COMMON_CRAWL_QUERIES.get(platform, []):
                cdx_url = build_cdx_url(index["cdx_api"], url_pattern, limit)
                print(f"Querying {index['id']}: {url_pattern}")
                body = fetch_text_urllib(cdx_url, timeout=60)
                if not body:
                    continue

                found = extract_slugs_from_text(body)
                new_slugs = found.get(platform, set()) - existing_slugs[platform]
                if new_slugs:
                    discoveries[platform].update(new_slugs)
                    existing_slugs[platform].update(new_slugs)
                    print(f"  + {platform}: {len(new_slugs):,} new slugs")
                else:
                    print(f"  + {platform}: no new slugs")

    return discoveries

def scan_company(company, existing_slugs):
    """Scan a single company website for career board links."""
    name = company.get("name", "Unknown")
    website = company.get("website")
    
    if not website:
        return name, {}
        
    # Clean/normalize website URL
    if not website.startswith("http"):
        website = "http://" + website
        
    # Grab HTML
    html = fetch_homepage_html(website)
    if not html:
        return name, {}
        
    # Extract slugs
    discovered = extract_slugs_from_html(html)
    
    # Filter out slugs we already have
    new_discoveries = {}
    for platform, slugs in discovered.items():
        for slug in slugs:
            if slug not in existing_slugs[platform]:
                if platform not in new_discoveries:
                    new_discoveries[platform] = []
                new_discoveries[platform].append(slug)
                
    return name, new_discoveries

def discover_yc_companies(existing_companies, workers=20):
    """Discover ATS slugs from YC company homepages."""
    print("=" * 80)
    print("Y COMBINATOR HOMEPAGE DISCOVERY")
    print("=" * 80)

    print("Fetching active/hiring companies list from Y Combinator directory...")
    # hiring.json is smaller, more focused, and guarantees companies with active jobs
    hiring_companies = fetch_json_urllib("https://yc-oss.github.io/api/companies/hiring.json")
    
    # Fallback/Combine with all launched companies if hiring fetch was successful
    all_companies = fetch_json_urllib("https://yc-oss.github.io/api/companies/all.json")
    
    companies_pool = []
    seen_websites = set()
    
    # Merge pools, prioritizing website uniqueness
    for source in [hiring_companies, all_companies]:
        if source and isinstance(source, list):
            for c in source:
                web = c.get("website")
                if web and web not in seen_websites:
                    seen_websites.add(web)
                    companies_pool.append(c)

    if not companies_pool:
        print("Could not retrieve company listings from YC API. Exiting.")
        return {platform: set() for platform in PLATFORM_FILES}

    print(f"Retrieved {len(companies_pool):,} unique company listings to scan.\n")
    print(f"Scanning startup websites in parallel ({workers} workers)... This might take 1-2 minutes.")
    print("-" * 80)

    new_listings = {platform: set() for platform in PLATFORM_FILES}
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(scan_company, company, existing_companies): company
            for company in companies_pool
        }
        
        scanned_count = 0
        for future in as_completed(futures):
            scanned_count += 1
            name, new_discoveries = future.result()
            
            if new_discoveries:
                print(f"[{scanned_count}/{len(companies_pool)}] Discovered for {name}:")
                for platform, slugs in new_discoveries.items():
                    for slug in slugs:
                        existing_companies[platform].add(slug)
                        new_listings[platform].add(slug)
                        print(f"  + [{platform.upper()}] slug: {slug}")
            
            if scanned_count % 100 == 0:
                print(f"  Progress: Scanned {scanned_count}/{len(companies_pool)} companies...")

    return new_listings

def save_discovered_companies(existing_companies, new_listings, dry_run=False):
    print("\n" + "=" * 80)
    print("SAVING NEW SLUGS")
    print("=" * 80)

    new_listings_count = {
        platform: len(slugs)
        for platform, slugs in new_listings.items()
    }
    
    total_added = sum(new_listings_count.values())
    print(f"Total new slugs discovered: {total_added}")

    if dry_run:
        print("Dry run enabled; company source files were not changed.")
        return
    
    for platform, count in new_listings_count.items():
        if count > 0:
            save_companies(platform, existing_companies[platform])
            print(f"  - {platform}: added {count} -> total {len(existing_companies[platform])} slugs")
        else:
            print(f"  - {platform}: no new slugs found")
            
    print("\nDiscovery completed successfully!")
    print("=" * 80)

def merge_discovery_counts(*discoveries):
    merged = {platform: set() for platform in PLATFORM_FILES}
    for discovery in discoveries:
        for platform, slugs in discovery.items():
            merged.setdefault(platform, set()).update(slugs)
    return merged

def main():
    parser = argparse.ArgumentParser(description="Discover public ATS company slugs.")
    parser.add_argument(
        "--source",
        choices=["all", "yc", "common-crawl"],
        default="all",
        help="Discovery source to run.",
    )
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(PLATFORM_FILES),
        help="Restrict discovery to one or more platforms. Can be passed multiple times.",
    )
    parser.add_argument(
        "--cc-indexes",
        type=int,
        default=2,
        help="Number of newest Common Crawl indexes to query.",
    )
    parser.add_argument(
        "--cc-limit",
        type=int,
        default=10000,
        help="Maximum CDX rows to request per Common Crawl query.",
    )
    parser.add_argument(
        "--yc-workers",
        type=int,
        default=20,
        help="Parallel workers for YC homepage scanning.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run discovery without writing company source files.",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("AUTOMATED COMPANY DISCOVERY")
    print("=" * 80)

    existing_companies = load_existing_companies()
    total_existing = sum(len(slugs) for slugs in existing_companies.values())
    print(f"Loaded {total_existing:,} existing companies across {len(PLATFORM_FILES)} platforms.")
    for p, s in existing_companies.items():
        print(f"  - {p}: {len(s):,} slugs")
    print()

    discoveries = []
    if args.source in ("all", "yc"):
        discoveries.append(discover_yc_companies(existing_companies, workers=args.yc_workers))

    if args.source in ("all", "common-crawl"):
        if args.platform:
            cc_platforms = tuple(args.platform)
        else:
            cc_platforms = DEFAULT_COMMON_CRAWL_PLATFORMS
        unsupported = [p for p in cc_platforms if p not in COMMON_CRAWL_QUERIES]
        if unsupported:
            print(f"Skipping Common Crawl for unsupported platforms: {', '.join(unsupported)}")
        cc_platforms = tuple(p for p in cc_platforms if p in COMMON_CRAWL_QUERIES)
        discoveries.append(
            discover_common_crawl(
                cc_platforms,
                existing_companies,
                index_count=args.cc_indexes,
                limit=args.cc_limit,
            )
        )

    save_discovered_companies(
        existing_companies,
        merge_discovery_counts(*discoveries),
        dry_run=args.dry_run,
    )

if __name__ == "__main__":
    main()
