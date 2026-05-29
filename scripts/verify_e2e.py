import sys
import os

# Add scripts directory to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

import scraper

E2E_COMPANY_LIMIT = int(os.environ.get("E2E_COMPANY_LIMIT", "2"))
E2E_FULL_PLATFORMS = {
    platform.strip().lower()
    for platform in os.environ.get("E2E_FULL_PLATFORMS", "").split(",")
    if platform.strip()
}

# Save original load_companies function
original_load_companies = scraper.load_companies

def mock_load_companies(filepath):
    companies = original_load_companies(filepath)
    filename = os.path.basename(filepath)

    if any(platform in filename for platform in E2E_FULL_PLATFORMS):
        return companies

    sliced = set(list(companies)[:E2E_COMPANY_LIMIT])
    print(
        f"  [E2E TEST SLICE] Slicing {filename} from {len(companies)} to {len(sliced)} companies"
    )
    return sliced

# Monkey-patch the load_companies function
scraper.load_companies = mock_load_companies

if __name__ == "__main__":
    print("=" * 80)
    print("RUNNING LIGHTNING-FAST END-TO-END VERIFICATION SCRAPER")
    print("=" * 80)
    
    scraper.SOURCE_TYPE = "manual"
    scraper.main()
    
    print("\n" + "=" * 80)
    print("END-TO-END VERIFICATION SCRAPER COMPLETED!")
    print("=" * 80)
