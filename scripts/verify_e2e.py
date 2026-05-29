import sys
import os

# Add scripts directory to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

import scraper

# Save original load_companies function
original_load_companies = scraper.load_companies

def mock_load_companies(filepath):
    companies = original_load_companies(filepath)
    filename = os.path.basename(filepath)
    
    # If it is one of our new platforms, keep all of them
    if any(p in filename for p in ["workable", "recruitee", "personio"]):
        return companies
        
    # Otherwise, slice it to a max of 2 companies for lightning fast end-to-end test!
    sliced = set(list(companies)[:2])
    print(f"  [E2E TEST SLICE] Slicing {filename} from {len(companies)} to {len(sliced)} companies")
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
