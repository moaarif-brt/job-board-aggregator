import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


ACTIVE = "active"
DEAD = "dead"
SUSPICIOUS = "suspicious"
UNVERIFIED = "unverified"

MIN_PUBLISH_HEALTH_SCORE = 20
REQUEST_TIMEOUT = 12
CACHE_SAVE_EVERY = 500
MAX_PENDING_FUTURES_MULTIPLIER = 20
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "gh_src",
    "lever-source",
    "source",
    "ref",
    "referrer",
    "src",
    "campaign",
}
VERIFICATION_FIELDS = {
    "verification_status",
    "job_health_score",
    "last_verified_at",
    "verification_reason",
    "verified_final_url",
    "verification_http_status",
    "job_fingerprint",
    "validation_cache_key",
}

ATS_STALE_DAYS = {
    "Workday": 3,
    "iCIMS": 2,
    "Lever": 5,
    "Ashby": 5,
    "Greenhouse": 7,
    "BambooHR": 5,
    "SmartRecruiters": 5,
    "Workable": 5,
    "Recruitee": 5,
    "JazzHR": 5,
    "Pinpoint": 5,
    "Personio": 5,
}

ACTIVE_CACHE_TTL_DAYS = {
    "Workday": 0.5,
    "iCIMS": 0.5,
    "Lever": 1,
    "Ashby": 1,
    "Greenhouse": 1,
    "BambooHR": 1,
    "SmartRecruiters": 1,
    "Workable": 1,
    "Recruitee": 1,
    "JazzHR": 1,
    "Pinpoint": 1,
    "Personio": 1,
}

EXPIRED_PATTERNS = [
    r"job\s+(?:is\s+)?no\s+longer\s+available",
    r"position\s+(?:is\s+)?no\s+longer\s+available",
    r"posting\s+(?:is\s+)?no\s+longer\s+available",
    r"job\s+not\s+found",
    r"opening\s+not\s+found",
    r"position\s+has\s+been\s+filled",
    r"posting\s+has\s+(?:expired|closed)",
    r"job\s+has\s+(?:expired|closed)",
    r"this\s+job\s+is\s+closed",
    r"this\s+position\s+is\s+closed",
    r"we\s+couldn.t\s+find\s+that\s+job",
    r"we\s+could\s+not\s+find\s+that\s+job",
    r"no\s+longer\s+accepting\s+applications",
    r"not\s+accepting\s+applications",
    r"applications\s+are\s+closed",
    r"this\s+role\s+is\s+closed",
    r"this\s+role\s+has\s+been\s+filled",
    r"this\s+vacancy\s+has\s+closed",
    r"this\s+vacancy\s+is\s+closed",
    r"this\s+opening\s+is\s+closed",
    r"the\s+page\s+you\s+requested\s+could\s+not\s+be\s+found",
    r"the\s+requested\s+job\s+could\s+not\s+be\s+found",
    r"opportunity\s+is\s+no\s+longer\s+available",
    r"job\s+you\s+are\s+looking\s+for\s+is\s+no\s+longer",
]

BOT_CHALLENGE_PATTERNS = [
    r"captcha",
    r"cf-chl",
    r"cloudflare",
    r"access\s+denied",
    r"request\s+blocked",
    r"verify\s+you\s+are\s+human",
    r"are\s+you\s+a\s+human",
    r"unusual\s+traffic",
    r"temporarily\s+blocked",
    r"akamai",
    r"perimeterx",
    r"datadome",
    r"bot\s+detection",
]

SOFT_INVALID_PATTERNS = [
    r"<title>\s*(?:not\s+found|404|error)",
    r"page\s+not\s+found",
    r"oops[,!\s]+(?:something\s+went\s+wrong|we\s+couldn.t\s+find)",
    r"search\s+all\s+jobs",
    r"browse\s+open\s+positions",
]

ATS_EXPIRED_PATTERNS = {
    "Greenhouse": [
        r"job\s+not\s+found",
        r"we\s+couldn.t\s+find\s+that\s+job",
        r"no\s+longer\s+available",
    ],
    "Ashby": [
        r"job\s+posting\s+not\s+found",
        r"this\s+job\s+posting\s+is\s+no\s+longer\s+available",
    ],
    "Lever": [
        r"posting\s+not\s+found",
        r"this\s+posting\s+is\s+no\s+longer\s+available",
    ],
    "Workday": [
        r"job\s+posting\s+not\s+found",
        r"no\s+longer\s+available",
        r"the\s+job\s+you\s+are\s+looking\s+for\s+is\s+no\s+longer\s+available",
    ],
    "BambooHR": [
        r"job\s+opening\s+not\s+found",
        r"job\s+not\s+found",
    ],
    "SmartRecruiters": [
        r"job\s+not\s+found",
        r"this\s+job\s+is\s+no\s+longer\s+available",
    ],
    "iCIMS": [
        r"job\s+not\s+found",
        r"the\s+job\s+you\s+are\s+looking\s+for\s+is\s+no\s+longer\s+available",
    ],
    "Workable": [
        r"job\s+not\s+found",
        r"this\s+job\s+is\s+no\s+longer\s+available",
    ],
    "Recruitee": [
        r"offer\s+not\s+found",
        r"job\s+not\s+found",
    ],
    "JazzHR": [
        r"job\s+not\s+found",
        r"position\s+has\s+been\s+closed",
    ],
    "Pinpoint": [
        r"posting\s+not\s+found",
        r"this\s+job\s+is\s+no\s+longer\s+available",
    ],
    "Personio": [
        r"job\s+not\s+found",
        r"position\s+not\s+found",
    ],
}

ATS_URL_HINTS = {
    "Greenhouse": ("greenhouse.io",),
    "Ashby": ("ashbyhq.com",),
    "Lever": ("lever.co",),
    "Workday": ("myworkdayjobs.com",),
    "BambooHR": ("bamboohr.com",),
    "SmartRecruiters": ("smartrecruiters.com",),
    "iCIMS": ("icims.com",),
    "Workable": ("workable.com",),
    "Recruitee": ("recruitee.com",),
    "JazzHR": ("jazz.co",),
    "Pinpoint": ("pinpointhq.com",),
    "Personio": ("personio.de", "personio.com"),
}

_thread_local = threading.local()
_host_locks = {}
_host_locks_guard = threading.Lock()
_host_last_request = {}


def host_lock(hostname):
    with _host_locks_guard:
        lock = _host_locks.get(hostname)
        if lock is None:
            lock = threading.Semaphore(4)
            _host_locks[hostname] = lock
        return lock


def throttle_host(hostname):
    if not hostname:
        return
    with _host_locks_guard:
        last_request = _host_last_request.get(hostname, 0)
        wait_for = 0.15 - (time.monotonic() - last_request)
        if wait_for > 0:
            time.sleep(wait_for)
        _host_last_request[hostname] = time.monotonic()


@dataclass
class ValidationResult:
    status: str
    score: int
    reason: str
    checked_url: str
    final_url: str
    http_status: int | None
    failure_count: int = 0


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(value):
    dt = parse_dt(value)
    if not dt:
        return None
    return (utc_now() - dt).total_seconds() / 86400


def normalized_ats(job):
    return job.get("ats") or "Unknown"


def normalize_text_value(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(str(url).strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or any(key_lower.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))
    query = urlencode(sorted(query_items))
    return urlunparse((scheme, netloc, path, "", query, ""))


def job_fingerprint(job):
    parts = [
        normalized_ats(job),
        normalize_url(job.get("url") or job.get("absolute_url")),
        normalize_text_value(job.get("company_slug") or job.get("company")),
        normalize_text_value(job.get("title")),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()


def stale_limit_days(job):
    return ATS_STALE_DAYS.get(normalized_ats(job), 5)


def active_cache_ttl_days(job):
    return ACTIVE_CACHE_TTL_DAYS.get(normalized_ats(job), 2)


def is_stale(job):
    scraped_age = age_days(job.get("scraped_at"))
    if scraped_age is None:
        return True
    return scraped_age > stale_limit_days(job)


def get_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/144.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
        _thread_local.session = session
    return session


def load_validation_cache(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_validation_cache(path, cache):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    tmp_path.replace(path)


def cache_key(job):
    return job_fingerprint(job)


def stamp_identity(job):
    fingerprint = job_fingerprint(job)
    job["job_fingerprint"] = fingerprint
    job["validation_cache_key"] = fingerprint
    return fingerprint


def copy_verification_fields(source, target):
    for field in VERIFICATION_FIELDS:
        if source.get(field) is not None:
            target[field] = source[field]
    return target


def cache_is_fresh(job, cached):
    checked_at = parse_dt(cached.get("last_verified_at"))
    if not checked_at:
        return False
    if cached.get("verification_status") == DEAD:
        return True
    if cached.get("verification_status") != ACTIVE:
        return False
    ttl = timedelta(days=active_cache_ttl_days(job))
    return utc_now() - checked_at <= ttl


def cached_identity_matches(job, cached):
    expected = job_fingerprint(job)
    return cached.get("job_fingerprint") in (None, expected) and cached.get("checked_url") in (
        None,
        normalize_url(job.get("url") or job.get("absolute_url")),
        job.get("url") or job.get("absolute_url"),
    )


def page_has_expired_text(ats, text):
    if not text:
        return False
    sample = text[:250_000].lower()
    patterns = EXPIRED_PATTERNS + ATS_EXPIRED_PATTERNS.get(ats, [])
    return any(re.search(pattern, sample, re.IGNORECASE) for pattern in patterns)


def page_has_bot_challenge(text):
    if not text:
        return False
    sample = text[:120_000].lower()
    return any(re.search(pattern, sample, re.IGNORECASE) for pattern in BOT_CHALLENGE_PATTERNS)


def page_has_soft_invalid_text(text):
    if not text:
        return False
    sample = text[:120_000].lower()
    return any(re.search(pattern, sample, re.IGNORECASE) for pattern in SOFT_INVALID_PATTERNS)


def looks_like_homepage_redirect(original_url, final_url, ats):
    if not final_url:
        return False
    original = urlparse(original_url)
    final = urlparse(final_url)
    if not final.netloc:
        return False
    if original.netloc and original.netloc != final.netloc:
        original_base = original.netloc.replace("www.", "")
        final_base = final.netloc.replace("www.", "")
        expected = ATS_URL_HINTS.get(ats, ())
        if not any(hint in final_base for hint in expected) and original_base != final_base:
            return True
    path = (final.path or "/").strip("/")
    original_path = (original.path or "/").strip("/")
    if original_path and not path:
        return True
    generic_paths = {"careers", "jobs", "job", "openings", "search", "home"}
    return path.lower() in generic_paths and original_path.lower() != path.lower()


def final_url_keeps_job_identity(original_url, final_url):
    original = urlparse(original_url)
    final = urlparse(final_url or "")
    if not final.netloc:
        return False
    original_parts = [part for part in original.path.lower().split("/") if part]
    final_path = final.path.lower()
    meaningful_parts = [
        part for part in original_parts
        if len(part) >= 6 and part not in {"jobs", "job", "careers", "apply", "postings"}
    ]
    if not meaningful_parts:
        return True
    return any(part in final_path for part in meaningful_parts[-2:])


def is_success_status(status_code):
    return 200 <= int(status_code or 0) < 300


def is_blocked_status(status_code):
    return int(status_code or 0) in {401, 403, 407, 408, 423, 425, 429, 451, 500, 502, 503, 504}


def precheck_response(job, response, text):
    ats = normalized_ats(job)
    if response is None:
        return DEAD, "request_failed"
    if is_blocked_status(response.status_code):
        return DEAD, f"{ats.lower()}_blocked_or_rate_limited_{response.status_code}"
    if not is_success_status(response.status_code):
        return DEAD, f"{ats.lower()}_non_success_{response.status_code}"
    if len(response.history) >= 4:
        return DEAD, f"{ats.lower()}_suspicious_redirect_chain"
    if page_has_bot_challenge(text):
        return DEAD, f"{ats.lower()}_bot_challenge"
    if page_has_soft_invalid_text(text):
        return DEAD, f"{ats.lower()}_soft_invalid_page"
    if looks_like_homepage_redirect(job["url"], response.url, ats):
        return DEAD, f"{ats.lower()}_homepage_redirect"
    if not final_url_keeps_job_identity(job["url"], response.url):
        return DEAD, f"{ats.lower()}_lost_job_identity_redirect"
    return None


def has_minimum_metadata(job):
    return bool(job.get("title") and (job.get("company") or job.get("company_slug")) and job.get("url"))


def base_health_score(job, status, reason, failure_count=0):
    score = 100
    scraped_age = age_days(job.get("scraped_at"))
    if scraped_age is None:
        score -= 25
    else:
        score -= min(30, int(scraped_age * 3))
    if not has_minimum_metadata(job):
        score -= 30
    if not job.get("coords"):
        score -= 3
    if status == SUSPICIOUS:
        score -= 35
    elif status == UNVERIFIED:
        score -= 45
    elif status == DEAD:
        score = 0
    if "redirect" in reason:
        score -= 20
    if "expired_text" in reason:
        score = 0
    score -= min(30, failure_count * 10)
    return max(0, min(100, score))


def validate_greenhouse(job, response, text):
    if page_has_expired_text("Greenhouse", text):
        return DEAD, "greenhouse_expired_text"
    if "/jobs/" not in (response.url or ""):
        return DEAD, "greenhouse_missing_job_path"
    return ACTIVE, "greenhouse_active"


def validate_workday(job, response, text):
    if page_has_expired_text("Workday", text):
        return DEAD, "workday_expired_text"
    if "myworkdayjobs.com" not in urlparse(response.url).netloc:
        return DEAD, "workday_external_redirect"
    return ACTIVE, "workday_active"


def validate_lever(job, response, text):
    if page_has_expired_text("Lever", text):
        return DEAD, "lever_archived_or_expired"
    if "lever.co" not in urlparse(response.url).netloc:
        return DEAD, "lever_external_redirect"
    return ACTIVE, "lever_active"


def validate_ashby(job, response, text):
    if page_has_expired_text("Ashby", text):
        return DEAD, "ashby_removed_posting"
    if "ashbyhq.com" not in urlparse(response.url).netloc:
        return DEAD, "ashby_external_redirect"
    return ACTIVE, "ashby_active"


def validate_icims(job, response, text):
    if page_has_expired_text("iCIMS", text):
        return DEAD, "icims_invalid_or_expired"
    return ACTIVE, "icims_active"


def validate_generic_ats(job, response, text):
    ats = normalized_ats(job)
    if page_has_expired_text(ats, text):
        return DEAD, f"{ats.lower()}_expired_text"
    return ACTIVE, f"{ats.lower()}_active"


ATS_VALIDATORS = {
    "Greenhouse": validate_greenhouse,
    "Workday": validate_workday,
    "Lever": validate_lever,
    "Ashby": validate_ashby,
    "iCIMS": validate_icims,
}


def request_job_url(url):
    session = get_session()
    hostname = urlparse(url).netloc.lower()
    lock = host_lock(hostname)
    try:
        with lock:
            throttle_host(hostname)
            return session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None


def greenhouse_api_preflight(job):
    job_id = job.get("id")
    company = job.get("company_slug") or job.get("company")
    if not job_id or not company:
        return None
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    response = request_job_url(url)
    if response is None:
        return None
    if response.status_code in (404, 410):
        return ValidationResult(DEAD, 0, "greenhouse_missing_opening_id", url, response.url, response.status_code, 1)
    if response.status_code == 200:
        result = ValidationResult(ACTIVE, base_health_score(job, ACTIVE, "greenhouse_api_active"), url, response.url, response.status_code, 0)
        return result
    return None


def ats_preflight(job):
    if normalized_ats(job) == "Greenhouse":
        return greenhouse_api_preflight(job)
    return None


def validate_job_url(job, cached=None):
    url = job.get("url") or job.get("absolute_url")
    ats = normalized_ats(job)
    failure_count = int((cached or {}).get("failure_count", 0))

    if not url or not str(url).startswith(("http://", "https://")):
        return ValidationResult(DEAD, 0, "invalid_url", url or "", "", None, failure_count + 1)

    preflight = ats_preflight(job)
    if preflight and preflight.status == DEAD:
        preflight.failure_count = failure_count + 1
        return preflight

    response = request_job_url(url)
    if response is None:
        status = DEAD
        reason = "request_failed"
        failure_count += 1
        return ValidationResult(
            status,
            base_health_score(job, status, reason, failure_count),
            reason,
            url,
            "",
            None,
            failure_count,
        )

    content_type = response.headers.get("Content-Type", "")
    text = response.text if "text" in content_type or "html" in content_type or "xml" in content_type else ""
    precheck = precheck_response(job, response, text)
    if precheck:
        status, reason = precheck
        return ValidationResult(
            status,
            base_health_score(job, status, reason, failure_count + 1),
            reason,
            url,
            response.url,
            response.status_code,
            failure_count + 1,
        )

    validator = ATS_VALIDATORS.get(ats, validate_generic_ats)
    status, reason = validator(job, response, text)
    if status == ACTIVE:
        failure_count = 0
    else:
        failure_count += 1

    return ValidationResult(
        status,
        base_health_score(job, status, reason, failure_count),
        reason,
        url,
        response.url,
        response.status_code,
        failure_count,
    )


def apply_cached_result(job, cached):
    stamp_identity(job)
    job["verification_status"] = cached.get("verification_status", UNVERIFIED)
    job["job_health_score"] = int(cached.get("job_health_score", 0))
    job["last_verified_at"] = cached.get("last_verified_at")
    job["verification_reason"] = cached.get("verification_reason")
    return job


def apply_validation_result(job, result):
    stamp_identity(job)
    job["verification_status"] = result.status
    job["job_health_score"] = result.score
    job["last_verified_at"] = iso_now()
    job["verification_reason"] = result.reason
    if result.final_url:
        job["verified_final_url"] = result.final_url
    if result.http_status is not None:
        job["verification_http_status"] = result.http_status
    return job


def cache_record(result, fingerprint=None):
    return {
        "verification_status": result.status,
        "job_health_score": result.score,
        "last_verified_at": iso_now(),
        "verification_reason": result.reason,
        "checked_url": normalize_url(result.checked_url),
        "final_url": result.final_url,
        "http_status": result.http_status,
        "failure_count": result.failure_count,
        "job_fingerprint": fingerprint,
    }


def should_publish(job, min_health_score=MIN_PUBLISH_HEALTH_SCORE):
    if job.get("verification_status") != ACTIVE:
        return False
    if not cache_is_fresh(job, {"last_verified_at": job.get("last_verified_at"), "verification_status": ACTIVE}):
        return False
    return int(job.get("job_health_score") or 0) >= min_health_score


def iter_batches(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def validate_jobs(
    jobs,
    cache_path,
    workers=32,
    min_health_score=MIN_PUBLISH_HEALTH_SCORE,
    require_active=True,
):
    cache = load_validation_cache(cache_path)
    pending = []
    validated = []
    stats = {
        "input": len(jobs),
        "cache_hits": 0,
        "fast_path_active": 0,
        "checked": 0,
        "published": 0,
        "dropped_dead": 0,
        "dropped_suspicious": 0,
        "dropped_unverified": 0,
        "dropped_low_health": 0,
    }

    for job in jobs:
        key = stamp_identity(job)
        cached = cache.get(key)
        if cached and cached_identity_matches(job, cached) and cache_is_fresh(job, cached):
            apply_cached_result(job, cached)
            stats["cache_hits"] += 1
            stats["fast_path_active"] += 1 if job.get("verification_status") == ACTIVE else 0
            validated.append(job)
        else:
            pending.append((key, job, cached))

    print(
        f"Validation: {stats['cache_hits']:,} cache hits, "
        f"{len(pending):,} jobs require URL checks"
    )

    completed_since_save = 0
    completed_total = 0
    max_pending_futures = max(workers * MAX_PENDING_FUTURES_MULTIPLIER, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in iter_batches(pending, max_pending_futures):
            futures = {
                executor.submit(validate_job_url, job, cached): (key, job)
                for key, job, cached in batch
            }
            for future in as_completed(futures):
                key, job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failure_count = int((cache.get(key) or {}).get("failure_count", 0)) + 1
                    result = ValidationResult(
                        DEAD,
                        0,
                        f"validator_exception:{type(exc).__name__}",
                        job.get("url") or "",
                        "",
                        None,
                        failure_count,
                    )
                apply_validation_result(job, result)
                cache[key] = cache_record(result, fingerprint=key)
                validated.append(job)
                stats["checked"] += 1
                completed_since_save += 1
                completed_total += 1
                if completed_since_save >= CACHE_SAVE_EVERY:
                    save_validation_cache(cache_path, cache)
                    completed_since_save = 0
                    print(f"Validation progress: {completed_total:,}/{len(pending):,} checked")
                time.sleep(0.002)

    save_validation_cache(cache_path, cache)

    publishable = []
    for job in validated:
        status = job.get("verification_status")
        score = int(job.get("job_health_score") or 0)
        if require_active and status != ACTIVE:
            if status == DEAD:
                stats["dropped_dead"] += 1
            elif status == SUSPICIOUS:
                stats["dropped_suspicious"] += 1
            else:
                stats["dropped_unverified"] += 1
            continue
        if score < min_health_score:
            stats["dropped_low_health"] += 1
            continue
        publishable.append(job)

    stats["published"] = len(publishable)
    return publishable, stats
