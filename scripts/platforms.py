from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DATA_DIR = ROOT_DIR / "data"

PLATFORMS = {
    "greenhouse": {
        "label": "GREENHOUSE",
        "ats": "Greenhouse",
        "company_file": DATA_DIR / "greenhouse_companies.json",
    },
    "ashby": {
        "label": "ASHBY",
        "ats": "Ashby",
        "company_file": DATA_DIR / "ashby_companies.json",
    },
    "bamboohr": {
        "label": "BAMBOOHR",
        "ats": "BambooHR",
        "company_file": DATA_DIR / "bamboohr_companies.json",
    },
    "lever": {
        "label": "LEVER",
        "ats": "Lever",
        "company_file": DATA_DIR / "lever_companies.json",
    },
    "workday": {
        "label": "WORKDAY",
        "ats": "Workday",
        "company_file": DATA_DIR / "workday_companies.json",
    },
    "icims": {
        "label": "iCIMS",
        "ats": "iCIMS",
        "company_file": DATA_DIR / "icims_companies.json",
    },
    "workable": {
        "label": "WORKABLE",
        "ats": "Workable",
        "company_file": DATA_DIR / "workable_companies.json",
    },
    "recruitee": {
        "label": "RECRUITEE",
        "ats": "Recruitee",
        "company_file": DATA_DIR / "recruitee_companies.json",
    },
    "personio": {
        "label": "PERSONIO",
        "ats": "Personio",
        "company_file": DATA_DIR / "personio_companies.json",
    },
    "smartrecruiters": {
        "label": "SMARTRECRUITERS",
        "ats": "SmartRecruiters",
        "company_file": DATA_DIR / "smartrecruiters_companies.json",
    },
    "jazzhr": {
        "label": "JAZZHR",
        "ats": "JazzHR",
        "company_file": DATA_DIR / "jazzhr_companies.json",
    },
}

DEFAULT_PRIORITIES = {"daily", "every_3_days"}
SCRAPE_STATUSES = {"active", "empty", "flaky"}
SKIP_STATUSES = {"dead", "paused"}


def registry_dir(platform):
    return DATA_DIR / "companies" / platform


def raw_candidates_file(platform):
    return registry_dir(platform) / "raw_candidates.json"


def validated_file(platform):
    return registry_dir(platform) / "validated.json"


def status_file(platform):
    return DATA_DIR / "company_status" / f"{platform}.json"


def platform_names():
    return tuple(PLATFORMS.keys())
