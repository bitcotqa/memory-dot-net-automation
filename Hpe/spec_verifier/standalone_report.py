#!/usr/bin/env python3
import csv
import os

# Input CSV
input_file = 'scraped_specifications (1).csv'
output_file = 'HPE_Final_Report.csv'

print("Reading input CSV...")
rows_list = []

try:
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, 1):
            # Parse specifications
            specs_str = row.get('server_specifications', '{}')
            try:
                specs_dict = eval(specs_str) if specs_str.startswith('{') else {}
            except:
                specs_dict = {}
            
            # Count sections
            if isinstance(specs_dict, dict):
                num_sections = len(specs_dict)
                section_names = ', '.join(list(specs_dict.keys())[:2])
            else:
                num_sections = 0
                section_names = 'None'
            
            # Check completeness
            has_desc = bool(row.get('server_description', '').strip())
            has_url = bool(row.get('new_pdf_number', '').strip())
            has_specs = num_sections > 0
            
            completeness = (sum([has_desc, has_url, has_specs]) / 3) * 100
            
            # Create report row
            report_row = {
                'Row': idx,
                'Server_Name': row.get('server_description', '')[:80],
                'PDF_URL': row.get('new_pdf_number', '')[:60],
                'Spec_Sections_Count': num_sections,
                'Spec_Section_Names': section_names[:60],
                'Data_Complete': 'Yes' if completeness == 100 else 'Partial',
                'Completeness_Percent': f'{completeness:.1f}'
            }
            rows_list.append(report_row)
    
    print(f"Processed {len(rows_list)} rows")
    
    # Write output CSV
    print(f"Writing report to: {output_file}")
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['Row', 'Server_Name', 'PDF_URL', 'Spec_Sections_Count', 'Spec_Section_Names', 'Data_Complete', 'Completeness_Percent']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_list)
    
    print(f"\n✓ SUCCESS: Report generated!")
    print(f"  File: {output_file}")
    print(f"  Records: {len(rows_list)}")
    
    # Summary stats
    complete_count = sum(1 for r in rows_list if r['Data_Complete'] == 'Yes')
    print(f"  Complete: {complete_count}/{len(rows_list)}")
    
except Exception as e:
    print(f"ERROR: {str(e)}")
    import traceback
    traceback.print_exc()
