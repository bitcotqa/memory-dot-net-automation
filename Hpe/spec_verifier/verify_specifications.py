"""
HPE Server Specifications Verification Script
Automates verification of server specifications from PDF URLs against CSV data
"""

import csv
import json
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import logging
import re
import time
from pathlib import Path
from difflib import SequenceMatcher
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('verification_log.txt'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SpecificationVerifier:
    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.session = self._create_session()
        self.results = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
    def _create_session(self):
        """Create session with retry strategy"""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 504)
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session
    
    def load_csv(self):
        """Load CSV file and return dataframe"""
        try:
            df = pd.read_csv(self.csv_path)
            logger.info(f"✓ Loaded CSV with {len(df)} server records")
            return df
        except Exception as e:
            logger.error(f"✗ Error loading CSV: {str(e)}")
            raise
    
    def fetch_url_content(self, url):
        """Fetch HTML content from URL with timeout and error handling"""
        try:
            logger.info(f"  Fetching: {url}")
            response = self.session.get(
                url,
                headers=self.headers,
                timeout=15,
                verify=True
            )
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            logger.warning(f"  ✗ Failed to fetch URL: {str(e)[:100]}")
            return None
    
    def extract_technical_specs(self, html_content):
        """Extract technical specifications from HTML"""
        if not html_content:
            return None
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            specs = {}
            
            # Look for common spec patterns
            spec_sections = [
                'Technical Specifications',
                'System Unit',
                'Dimensions',
                'Weight',
                'Power',
                'Input Requirements',
                'BTU Rating'
            ]
            
            # Try to find text containing technical specs
            text_content = soup.get_text(separator='\n')
            
            for section in spec_sections:
                if section.lower() in text_content.lower():
                    # Extract the section content
                    start_idx = text_content.lower().find(section.lower())
                    if start_idx != -1:
                        end_idx = start_idx + 2000  # Get 2000 chars of content
                        specs[section] = text_content[start_idx:end_idx].strip()
            
            return specs if specs else None
            
        except Exception as e:
            logger.warning(f"  ✗ Error parsing HTML: {str(e)[:100]}")
            return None
    
    def parse_csv_specs(self, spec_string):
        """Parse specifications from CSV column"""
        try:
            if isinstance(spec_string, str) and spec_string.startswith('{'):
                # It's a dictionary string
                spec_dict = eval(spec_string)
                return spec_dict
            return None
        except:
            return None
    
    def compare_specifications(self, csv_specs, fetched_specs):
        """Compare CSV specs with fetched specs"""
        comparison = {
            'csv_specs': csv_specs,
            'fetched_specs': fetched_specs,
            'matches': [],
            'missing': [],
            'mismatches': [],
            'confidence_score': 0
        }
        
        if not csv_specs or not fetched_specs:
            comparison['confidence_score'] = 0 if not fetched_specs else 50
            return comparison
        
        # Check if key sections exist
        csv_keys = set(csv_specs.keys()) if isinstance(csv_specs, dict) else set()
        fetched_keys = set(fetched_specs.keys()) if isinstance(fetched_specs, dict) else set()
        
        # Calculate matches
        if csv_keys:
            matches = csv_keys.intersection(fetched_keys)
            missing = csv_keys - fetched_keys
            
            comparison['matches'] = list(matches)
            comparison['missing'] = list(missing)
            
            # Calculate confidence score
            if csv_keys:
                confidence = (len(matches) / len(csv_keys)) * 100
                comparison['confidence_score'] = round(confidence, 1)
            
            # Check for content similarity
            if matches:
                for key in matches:
                    csv_val = str(csv_specs.get(key, ''))[:200]
                    fetched_val = str(fetched_specs.get(key, ''))[:200]
                    
                    similarity = SequenceMatcher(None, csv_val, fetched_val).ratio()
                    if similarity < 0.5:
                        comparison['mismatches'].append({
                            'key': key,
                            'similarity': round(similarity * 100, 1)
                        })
        
        return comparison
    
    def verify_server(self, idx, row):
        """Verify a single server specification"""
        logger.info(f"\n[{idx}] Processing: {row['server_description']}")
        
        result = {
            'index': idx,
            'server_description': row['server_description'],
            'new_pdf_number': row['new_pdf_number'],
            'status': 'PENDING',
            'fetch_success': False,
            'parse_success': False,
            'confidence_score': 0,
            'matches': [],
            'missing': [],
            'mismatches': [],
            'error': None
        }
        
        # Fetch URL
        html_content = self.fetch_url_content(row['new_pdf_number'])
        
        if not html_content:
            result['status'] = 'FAILED'
            result['error'] = 'Failed to fetch URL'
            return result
        
        result['fetch_success'] = True
        
        # Extract specs from fetched content
        fetched_specs = self.extract_technical_specs(html_content)
        
        # Parse CSV specs
        csv_specs = self.parse_csv_specs(row['server_specifications'])
        
        if fetched_specs:
            result['parse_success'] = True
        
        # Compare
        comparison = self.compare_specifications(csv_specs, fetched_specs)
        
        result['confidence_score'] = comparison['confidence_score']
        result['matches'] = comparison['matches']
        result['missing'] = comparison['missing']
        result['mismatches'] = comparison['mismatches']
        
        # Determine status
        if result['confidence_score'] >= 75:
            result['status'] = 'VERIFIED ✓'
        elif result['confidence_score'] >= 50:
            result['status'] = 'PARTIAL ⚠'
        else:
            result['status'] = 'FAILED ✗'
        
        logger.info(f"  Status: {result['status']} (Confidence: {result['confidence_score']}%)")
        
        return result
    
    def generate_reports(self, results):
        """Generate CSV and HTML reports"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # CSV Report
        csv_report_path = f'verification_report_{timestamp}.csv'
        csv_data = []
        
        for r in results:
            csv_data.append({
                'Index': r['index'],
                'Server': r['server_description'],
                'URL': r['new_pdf_number'],
                'Status': r['status'],
                'Confidence': f"{r['confidence_score']}%",
                'Fetch': 'OK' if r['fetch_success'] else 'FAILED',
                'Parse': 'OK' if r['parse_success'] else 'FAILED',
                'Matches': len(r['matches']),
                'Missing': len(r['missing']),
                'Errors': r['error'] or 'None'
            })
        
        df = pd.DataFrame(csv_data)
        df.to_csv(csv_report_path, index=False)
        logger.info(f"\n✓ CSV Report saved: {csv_report_path}")
        
        # HTML Report
        html_report_path = f'verification_report_{timestamp}.html'
        self._generate_html_report(results, html_report_path)
        logger.info(f"✓ HTML Report saved: {html_report_path}")
        
        # Summary
        self._print_summary(results)
        
        return csv_report_path, html_report_path
    
    def _generate_html_report(self, results, filename):
        """Generate detailed HTML report"""
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>HPE Specifications Verification Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 3px solid #00a4ef; padding-bottom: 10px; }}
        h2 {{ color: #00a4ef; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; background-color: white; margin-bottom: 20px; }}
        th {{ background-color: #00a4ef; color: white; padding: 12px; text-align: left; }}
        td {{ border: 1px solid #ddd; padding: 12px; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        .status-verified {{ background-color: #d4edda; color: #155724; font-weight: bold; }}
        .status-partial {{ background-color: #fff3cd; color: #856404; font-weight: bold; }}
        .status-failed {{ background-color: #f8d7da; color: #721c24; font-weight: bold; }}
        .summary {{ background-color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        .summary-stat {{ display: inline-block; margin-right: 30px; }}
        .stat-number {{ font-size: 24px; font-weight: bold; color: #00a4ef; }}
        .stat-label {{ color: #666; font-size: 14px; }}
    </style>
</head>
<body>
    <h1>HPE Server Specifications Verification Report</h1>
    <p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    
    <div class="summary">
        <h2>Summary</h2>
        <div class="summary-stat">
            <div class="stat-number">{len([r for r in results if 'VERIFIED' in r['status']])}</div>
            <div class="stat-label">Verified ✓</div>
        </div>
        <div class="summary-stat">
            <div class="stat-number">{len([r for r in results if 'PARTIAL' in r['status']])}</div>
            <div class="stat-label">Partial ⚠</div>
        </div>
        <div class="summary-stat">
            <div class="stat-number">{len([r for r in results if 'FAILED' in r['status']])}</div>
            <div class="stat-label">Failed ✗</div>
        </div>
        <div class="summary-stat">
            <div class="stat-number">{len(results)}</div>
            <div class="stat-label">Total</div>
        </div>
    </div>
    
    <h2>Detailed Results</h2>
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Server</th>
                <th>Status</th>
                <th>Confidence</th>
                <th>Matches</th>
                <th>Missing</th>
                <th>URL Status</th>
            </tr>
        </thead>
        <tbody>
"""
        
        for r in results:
            status_class = 'status-verified' if 'VERIFIED' in r['status'] else \
                          'status-partial' if 'PARTIAL' in r['status'] else 'status-failed'
            
            html_content += f"""
            <tr>
                <td>{r['index']}</td>
                <td><strong>{r['server_description']}</strong><br/><small>{r['new_pdf_number'][:60]}...</small></td>
                <td class="{status_class}">{r['status']}</td>
                <td>{r['confidence_score']}%</td>
                <td>{len(r['matches'])}</td>
                <td>{len(r['missing'])}</td>
                <td>{'✓ Fetched' if r['fetch_success'] else '✗ Failed'}</td>
            </tr>
"""
        
        html_content += """
        </tbody>
    </table>
</body>
</html>
"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
    
    def _print_summary(self, results):
        """Print summary statistics"""
        verified = len([r for r in results if 'VERIFIED' in r['status']])
        partial = len([r for r in results if 'PARTIAL' in r['status']])
        failed = len([r for r in results if 'FAILED' in r['status']])
        
        print("\n" + "="*70)
        print("VERIFICATION SUMMARY".center(70))
        print("="*70)
        print(f"✓ VERIFIED:        {verified}/{len(results)}")
        print(f"⚠ PARTIAL:         {partial}/{len(results)}")
        print(f"✗ FAILED:          {failed}/{len(results)}")
        print(f"\nAverage Confidence: {round(sum(r['confidence_score'] for r in results)/len(results), 1)}%")
        print("="*70 + "\n")
    
    def run(self):
        """Main execution method"""
        logger.info("Starting HPE Server Specifications Verification")
        logger.info("="*70)
        
        try:
            # Load data
            df = self.load_csv()
            
            # Process each server
            for idx, (_, row) in enumerate(df.iterrows(), 1):
                result = self.verify_server(idx, row)
                self.results.append(result)
                time.sleep(2)  # Be respectful to server - 2 second delay
            
            # Generate reports
            csv_report, html_report = self.generate_reports(self.results)
            
            logger.info("="*70)
            logger.info("✓ Verification complete!")
            logger.info(f"Reports generated:")
            logger.info(f"  - CSV: {csv_report}")
            logger.info(f"  - HTML: {html_report}")
            
            return self.results
            
        except Exception as e:
            logger.error(f"✗ Fatal error: {str(e)}")
            raise


def main():
    """Main entry point"""
    csv_file = 'scraped_specifications (1).csv'

    if not Path(csv_file).exists():
        logger.error(f"✗ CSV file not found: {csv_file}")
        return
    
    verifier = SpecificationVerifier(csv_file)
    results = verifier.run()


if __name__ == "__main__":
    main()
