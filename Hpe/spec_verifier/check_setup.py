"""
Verification Setup and Dependencies Checker
Ensures all requirements are met before running the main script
"""

import sys
import subprocess
import os
from pathlib import Path

def check_python_version():
    """Check if Python version is 3.8+"""
    print("✓ Checking Python version...")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print(f"  ✓ Python {version.major}.{version.minor}.{version.micro} - OK")
        return True
    else:
        print(f"  ✗ Python {version.major}.{version.minor}.{version.micro} - FAILED (need 3.8+)")
        return False

def check_csv_file():
    """Check if CSV file exists"""
    print("\n✓ Checking CSV file...")
    csv_path = 'scraped_specifications (1).csv'
    if Path(csv_path).exists():
        size_mb = Path(csv_path).stat().st_size / (1024 * 1024)
        print(f"  ✓ CSV file found ({size_mb:.2f} MB)")
        return True
    else:
        print(f"  ✗ CSV file not found at {csv_path}")
        return False

def check_dependencies():
    """Check if required packages are installed"""
    print("\n✓ Checking dependencies...")
    required = ['requests', 'beautifulsoup4', 'pandas', 'urllib3']
    missing = []
    
    for package in required:
        try:
            __import__(package.replace('-', '_'))
            print(f"  ✓ {package} - installed")
        except ImportError:
            print(f"  ✗ {package} - NOT installed")
            missing.append(package)
    
    return len(missing) == 0, missing

def install_dependencies(missing):
    """Install missing dependencies"""
    if not missing:
        return True
    
    print("\n✓ Installing missing packages...")
    try:
        for package in missing:
            print(f"  Installing {package}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', package])
        print("  ✓ All packages installed successfully")
        return True
    except Exception as e:
        print(f"  ✗ Installation failed: {str(e)}")
        return False

def check_required_scripts():
    """Check if required scripts exist"""
    print("\n✓ Checking required scripts...")
    required_files = [
        'verify_specifications.py',
        'requirements.txt',
        'README.md'
    ]
    
    all_exist = True
    for fname in required_files:
        fpath = Path(fname)
        if fpath.exists():
            print(f"  ✓ {fname} - found")
        else:
            print(f"  ✗ {fname} - NOT found")
            all_exist = False
    
    return all_exist

def print_summary(checks):
    """Print summary of checks"""
    print("\n" + "="*60)
    print("SETUP VERIFICATION SUMMARY".center(60))
    print("="*60)
    
    all_pass = all(checks.values())
    
    if all_pass:
        print("✓ All checks passed! Ready to run verification.")
        print("\nQuick start:")
        print("  python verify_specifications.py")
    else:
        failed = [k for k, v in checks.items() if not v]
        print(f"✗ {len(failed)} check(s) failed:")
        for item in failed:
            print(f"  - {item}")
    
    print("="*60 + "\n")
    return all_pass

def main():
    """Main setup verification"""
    print("="*60)
    print("HPE SPECIFICATIONS VERIFICATION - SETUP CHECK".center(60))
    print("="*60 + "\n")
    
    checks = {
        'Python Version': check_python_version(),
        'CSV File': check_csv_file(),
        'Required Scripts': check_required_scripts(),
    }
    
    # Check dependencies
    deps_ok, missing = check_dependencies()
    checks['Dependencies'] = deps_ok
    
    # Install missing if any
    if missing:
        print("\nAttempting to install missing packages...")
        checks['Dependencies'] = install_dependencies(missing)
    
    # Print summary
    ready = print_summary(checks)
    
    if ready:
        print("Next steps:")
        print("1. Review README.md for detailed instructions")
        print("2. Run: python verify_specifications.py")
        print("3. Check verification_report_*.csv and .html for results")
        return 0
    else:
        print("Please fix the issues above before running the verification script.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
