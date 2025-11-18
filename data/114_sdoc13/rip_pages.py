import os
import subprocess

def rip_pages(file_name, start_page, end_page):
    """Extract individual pages from PDF using pdftotext with layout preservation."""
    # Create pages directory if it doesn't exist
    os.makedirs("pages", exist_ok=True)

    for page_number in range(start_page, end_page + 1):
        output_filename = f"pages/layout_{page_number}.txt"
        layout_cmd = ["pdftotext", "-f", str(page_number), "-l", str(page_number),
                      "-layout", file_name, output_filename]
        print(f"Extracting page {page_number}: {' '.join(layout_cmd)}")
        try:
            result = subprocess.run(layout_cmd, capture_output=True, text=True, check=True)
            if result.stderr:
                print(result.stderr)
        except subprocess.CalledProcessError as e:
            print(f"Error extracting page {page_number}: {e}")
            if e.stderr:
                print(e.stderr)