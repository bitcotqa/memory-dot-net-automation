#!/usr/bin/env python3
"""Investigate why partial record has 0 specs"""

import requests
from bs4 import BeautifulSoup

url = "https://www.hpe.com/psnow/doc/a00056109enw.html"

print("=" * 70)
print("INVESTIGATING PARTIAL RECORD")
print("=" * 70)
print(f"\nURL: {url}\n")

try:
    # Fetch the page
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    
    print(f"✓ Status Code: {response.status_code}")
    print(f"✓ Content-Type: {response.headers.get('content-type', 'N/A')}")
    print(f"✓ Content Length: {len(response.content):,} bytes")
    print(f"✓ Page Title: {response.history[0].url if response.history else url}")
    
    # Parse HTML
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Look for specification sections
    print("\n" + "-" * 70)
    print("LOOKING FOR SPECIFICATION SECTIONS")
    print("-" * 70)
    
    # Try different patterns
    patterns = [
        ('Text with "Specifications"', soup.find_all(string=lambda x: x and 'specification' in x.lower())),
        ('H1-H4 with "Specification"', soup.find_all(['h1', 'h2', 'h3', 'h4'], string=lambda x: x and 'specification' in x.lower())),
        ('All H2 tags', soup.find_all('h2')),
        ('All H3 tags', soup.find_all('h3')),
        ('All divs with "spec"', soup.find_all('div', class_=lambda x: x and 'spec' in x.lower() if x else False)),
    ]
    
    for pattern_name, elements in patterns:
        print(f"\n{pattern_name}: {len(elements)} found")
        for elem in elements[:3]:
            text = elem.text.strip() if hasattr(elem, 'text') else elem.strip()
            print(f"  • {text[:100]}")
    
    # Check page body
    print("\n" + "-" * 70)
    print("PAGE STRUCTURE")
    print("-" * 70)
    
    body_text = soup.get_text()[:500]
    print(f"First 500 chars of body text:\n{body_text}")
    
    # Check for table structures
    tables = soup.find_all('table')
    print(f"\nTables found: {len(tables)}")
    if tables:
        for i, table in enumerate(tables[:2]):
            print(f"\nTable {i+1}:")
            print(f"  Rows: {len(table.find_all('tr'))}")
            print(f"  First row: {[cell.text.strip()[:30] for cell in table.find_all('tr')[0].find_all(['th', 'td'])][:5]}")
    
except requests.RequestException as e:
    print(f"✗ Network Error: {e}")
except Exception as e:
    print(f"✗ Error: {type(e).__name__}: {e}")

print("\n" + "=" * 70)
