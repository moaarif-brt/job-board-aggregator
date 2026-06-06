import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


ACTIVE = "active"
DEAD = "dead"
SUSPICIOUS = "suspicious"
UNVERIFIED = "unverified"

MIN_PUBLISH_HEALTH_SCORE = 70
REQUEST_TIMEOUT = 12
CACHE_SAVE_EVERY = 500

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
    "Workday": 1,
    "iCIMS": 1,
    "Lever": 2,
    "Ashby": 2,
    "Greenhouse": 2,
    "BambooHR": 2,
    "SmartRecruiters": 2,
    "Workable": 2,
    "Recruitee": 2,
    "JazzHR": 2,
    "Pinpoint": 2,
    "Personio": 2,
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
    url = job.get("url") or job.get("absolute_url") or ""
    ats = normalized_ats(job)
    company = job.get("company_slug") or job.get("company") or ""
    return f"{ats}|{company}|{url}"


def cache_is_fresh(job, cached):
    checked_at = parse_dt(cached.get("last_verified_at"))
    if not checked_at:
        return False
    if cached.get("verification_status") == DEAD:
        return True
    ttl = timedelta(days=active_cache_ttl_days(job))
    return utc_now() - checked_at <= ttl


def page_has_expired_text(ats, text):
    if not text:
        return False
    sample = text[:250_000].lower()
    patterns = EXPIRED_PATTERNS + ATS_EXPIRED_PATTERNS.get(ats, [])
    return any(re.search(pattern, sample, re.IGNORECASE) for pattern in patterns)


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
    if response.status_code in (404, 410):
        return DEAD, "greenhouse_404"
    if page_has_expired_text("Greenhouse", text):
        return DEAD, "greenhouse_expired_text"
    if "/jobs/" not in (response.url or "") and looks_like_homepage_redirect(job["url"], response.url, "Greenhouse"):
        return SUSPICIOUS, "greenhouse_homepage_redirect"
    return ACTIVE, "greenhouse_active"


def validate_workday(job, response, text):
    if response.status_code in (404, 410):
        return DEAD, "workday_404"
    if len(response.history) >= 5:
        return SUSPICIOUS, "workday_redirect_chain"
    if page_has_expired_text("Workday", text):
        return DEAD, "workday_expired_text"
    if looks_like_homepage_redirect(job["url"], response.url, "Workday"):
        return SUSPICIOUS, "workday_homepage_redirect"
    return ACTIVE, "workday_active"


def validate_lever(job, response, text):
    if response.status_code in (404, 410):
        return DEAD, "lever_404"
    if page_has_expired_text("Lever", text):
        return DEAD, "lever_archived_or_expired"
    if looks_like_homepage_redirect(job["url"], response.url, "Lever"):
        return SUSPICIOUS, "lever_homepage_redirect"
    return ACTIVE, "lever_active"


def validate_ashby(job, response, text):
    if response.status_code in (404, 410):
        return DEAD, "ashby_404"
    if page_has_expired_text("Ashby", text):
        return DEAD, "ashby_removed_posting"
    if looks_like_homepage_redirect(job["url"], response.url, "Ashby"):
        return SUSPICIOUS, "ashby_homepage_redirect"
    return ACTIVE, "ashby_active"


def validate_icims(job, response, text):
    if response.status_code in (404, 410):
        return DEAD, "icims_404"
    if page_has_expired_text("iCIMS", text):
        return DEAD, "icims_invalid_or_expired"
    if looks_like_homepage_redirect(job["url"], response.url, "iCIMS"):
        return SUSPICIOUS, "icims_homepage_redirect"
    return ACTIVE, "icims_active"


def validate_generic_ats(job, response, text):
    ats = normalized_ats(job)
    if response.status_code in (404, 410):
        return DEAD, f"{ats.lower()}_404"
    if page_has_expired_text(ats, text):
        return DEAD, f"{ats.lower()}_expired_text"
    if looks_like_homepage_redirect(job["url"], response.url, ats):
        return SUSPICIOUS, f"{ats.lower()}_homepage_redirect"
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
        status = UNVERIFIED
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
    validator = ATS_VALIDATORS.get(ats, validate_generic_ats)
    status, reason = validator(job, response, text)
    if status == ACTIVE:
        failure_count = 0
    elif status in (DEAD, SUSPICIOUS):
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
    job["verification_status"] = cached.get("verification_status", UNVERIFIED)
    job["job_health_score"] = int(cached.get("job_health_score", 0))
    job["last_verified_at"] = cached.get("last_verified_at")
    job["verification_reason"] = cached.get("verification_reason")
    return job


def apply_validation_result(job, result):
    job["verification_status"] = result.status
    job["job_health_score"] = result.score
    job["last_verified_at"] = iso_now()
    job["verification_reason"] = result.reason
    if result.final_url:
        job["verified_final_url"] = result.final_url
    if result.http_status is not None:
        job["verification_http_status"] = result.http_status
    return job


def cache_record(result):
    return {
        "verification_status": result.status,
        "job_health_score": result.score,
        "last_verified_at": iso_now(),
        "verification_reason": result.reason,
        "checked_url": result.checked_url,
        "final_url": result.final_url,
        "http_status": result.http_status,
        "failure_count": result.failure_count,
    }


def should_publish(job, min_health_score=MIN_PUBLISH_HEALTH_SCORE):
    if job.get("verification_status") != ACTIVE:
        return False
    return int(job.get("job_health_score") or 0) >= min_health_score


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
        "checked": 0,
        "published": 0,
        "dropped_dead": 0,
        "dropped_suspicious": 0,
        "dropped_unverified": 0,
        "dropped_low_health": 0,
    }

    for job in jobs:
        key = cache_key(job)
        cached = cache.get(key)
        if cached and cache_is_fresh(job, cached):
            apply_cached_result(job, cached)
            stats["cache_hits"] += 1
            validated.append(job)
        else:
            pending.append((key, job, cached))

    print(
        f"Validation: {stats['cache_hits']:,} cache hits, "
        f"{len(pending):,} jobs require URL checks"
    )

    completed_since_save = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(validate_job_url, job, cached): (key, job)
            for key, job, cached in pending
        }
        for i, future in enumerate(as_completed(futures), 1):
            key, job = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failure_count = int((cache.get(key) or {}).get("failure_count", 0)) + 1
                result = ValidationResult(
                    UNVERIFIED,
                    base_health_score(job, UNVERIFIED, "validator_exception", failure_count),
                    f"validator_exception:{type(exc).__name__}",
                    job.get("url") or "",
                    "",
                    None,
                    failure_count,
                )
            apply_validation_result(job, result)
            cache[key] = cache_record(result)
            validated.append(job)
            stats["checked"] += 1
            completed_since_save += 1
            if completed_since_save >= CACHE_SAVE_EVERY:
                save_validation_cache(cache_path, cache)
                completed_since_save = 0
                print(f"Validation progress: {i:,}/{len(pending):,} checked")
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
