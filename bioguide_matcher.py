#!/usr/bin/env python3
"""
Bioguide ID Matcher for Senate Disbursements

This module downloads legislator data from the unitedstates/congress-legislators
repository and provides functions to match senator names from disbursement records
to their bioguide IDs.

Usage:
    from bioguide_matcher import BioguideIdMatcher

    matcher = BioguideIdMatcher()
    bioguide_id = matcher.get_bioguide_id("LAMAR ALEXANDER", 2014)
"""

import os
import re
import unicodedata
import yaml
import urllib.request
from datetime import datetime
from pathlib import Path

# Disbursement reports print some senators' formal legal names where
# congress-legislators records the name they serve under (or vice versa).
# Keys and values are both in _normalize_name() form; entries are applied
# after normalization and before matching, so term-year filtering still
# applies. New misses surface in each run's unmatched_senators.csv.
NAME_ALIASES = {
    "A MITCHELL MCCONNELL": "MITCH MCCONNELL",
    "CHRIS MURPHY": "CHRISTOPHER MURPHY",
    "THEODORE BUDD": "TED BUDD",
    "JOHN PETER RICKETTS": "PETE RICKETTS",
    "JOHN R THUNE": "JOHN THUNE",
    # 113sdoc2 prints "MAIZE HIRONO" -- a typo for "MAZIE". The matcher can't
    # infer a spelling correction; the alias makes it explicit.
    "MAIZE HIRONO": "MAZIE HIRONO",
}


class BioguideIdMatcher:
    """Match senator names to bioguide IDs using congress-legislators data."""

    LEGISLATORS_CURRENT_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-current.yaml"
    LEGISLATORS_HISTORICAL_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-historical.yaml"

    def __init__(self, cache_dir=".bioguide_cache"):
        """
        Initialize the matcher and load legislator data.

        Args:
            cache_dir: Directory to cache downloaded YAML files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.senators = []
        self._load_legislators()

    def _download_file(self, url, filename):
        """Download a file from URL to cache directory."""
        cache_path = self.cache_dir / filename

        # Use cached file if it exists and is less than 7 days old
        if cache_path.exists():
            age_days = (datetime.now().timestamp() - cache_path.stat().st_mtime) / 86400
            if age_days < 7:
                print(f"Using cached {filename} (age: {age_days:.1f} days)")
                return cache_path

        print(f"Downloading {filename}...")
        try:
            urllib.request.urlretrieve(url, cache_path)
            print(f"Downloaded {filename}")
        except Exception as e:
            print(f"Error downloading {filename}: {e}")
            if cache_path.exists():
                print(f"Using existing cached file despite error")
            else:
                raise

        return cache_path

    def _load_legislators(self):
        """Download and load legislator data from YAML files."""
        print("\n=== Loading legislator data ===")

        # Download files
        current_file = self._download_file(
            self.LEGISLATORS_CURRENT_URL,
            "legislators-current.yaml"
        )
        historical_file = self._download_file(
            self.LEGISLATORS_HISTORICAL_URL,
            "legislators-historical.yaml"
        )

        # Parse YAML files
        print("Parsing legislator data...")
        with open(current_file, 'r') as f:
            current_legislators = yaml.safe_load(f)

        with open(historical_file, 'r') as f:
            historical_legislators = yaml.safe_load(f)

        # Combine and filter for senators
        all_legislators = current_legislators + historical_legislators

        for legislator in all_legislators:
            # Get senator terms only
            senator_terms = [
                term for term in legislator.get('terms', [])
                if term.get('type') == 'sen'
            ]

            if senator_terms:
                # Extract relevant information
                bioguide_id = legislator['id'].get('bioguide', '')
                name_data = legislator.get('name', {})

                # Store senator info with their terms
                senator_info = {
                    'bioguide_id': bioguide_id,
                    'first_name': name_data.get('first', ''),
                    'last_name': name_data.get('last', ''),
                    'middle_name': name_data.get('middle', ''),
                    'nickname': name_data.get('nickname', ''),
                    'official_full': name_data.get('official_full', ''),
                    'suffix': name_data.get('suffix', ''),
                    'terms': senator_terms
                }

                self.senators.append(senator_info)

        print(f"Loaded {len(self.senators)} senators")

    def _normalize_name(self, name):
        """Normalize a name for comparison."""
        if not name:
            return ""

        # Strip accents (reports print "LUJAN"; the YAML has "Luján")
        name = unicodedata.normalize('NFKD', name)
        name = ''.join(c for c in name if not unicodedata.combining(c))

        # Convert to uppercase and remove extra whitespace
        name = name.upper().strip()

        # Strip disambiguating state suffix printed by the Senate for
        # same-last-name colleagues (e.g. "MARK UDALL (CO)" / "TOM UDALL
        # (NM)" in 112sdoc4-114sdoc4). Done before punctuation stripping so
        # the parens don't get smashed into the surname.
        name = re.sub(r'\s*\([A-Z]{2}\)\s*$', '', name)

        # Remove common suffixes
        name = re.sub(r'\s+(JR\.?|SR\.?|III?|IV|V)$', '', name)

        # Replace punctuation with space. "PATRICK J.TOOMEY" (no space after
        # the middle-initial period, as printed in 113sdoc2) must normalize
        # to "PATRICK J TOOMEY" -- removing the punctuation without a space
        # collapses it to "PATRICK JTOOMEY" and breaks the match.
        name = re.sub(r'[^\w\s]', ' ', name)

        # Normalize whitespace
        name = ' '.join(name.split())

        return name

    def _get_senator_full_name(self, senator):
        """Generate various name formats for a senator."""
        names = []

        first = senator['first_name']
        last = senator['last_name']
        middle = senator['middle_name']
        nickname = senator['nickname']
        official_full = senator.get('official_full', '')

        # First Last (most common format)
        if first and last:
            names.append(f"{first} {last}")

        # Nickname Last (if different from first)
        if nickname and nickname != first and last:
            names.append(f"{nickname} {last}")

        # First Middle Last
        if first and middle and last:
            names.append(f"{first} {middle} {last}")
            # Also try first initial of middle
            names.append(f"{first} {middle[0]} {last}")

        # official_full carries the name printed on the Senate floor and in
        # disbursement reports -- often a nickname or formal variant that
        # first/last/middle alone can't reconstruct. Scott Brown's YAML has
        # first='Scott' / last='Brown' (no middle) but official_full='Scott
        # P. Brown', so without this the reports' 'SCOTT P. BROWN' (and the
        # 80 unmatched rows across 112sdoc4-115sdoc6) don't resolve. Tom
        # Coburn's official_full='Tom Coburn' similarly carries the nickname
        # 'Tom' that the first='Thomas' record lacks.
        if official_full:
            names.append(official_full)

        # Normalize all names
        return [self._normalize_name(name) for name in names]

    def _is_senator_active(self, senator, year):
        """Check if a senator was active in a given year."""
        if not year:
            return True  # If no year provided, consider all senators

        for term in senator['terms']:
            start_str = term.get('start', '')
            end_str = term.get('end', '')

            if not start_str:
                continue

            # Parse dates
            try:
                start_year = int(start_str.split('-')[0])
                end_year = int(end_str.split('-')[0]) if end_str else datetime.now().year

                # `year` is a federal funding year, which runs Oct 1 of
                # year-1 through Sep 30 of year — and offices keep booking
                # expenses after a senator departs. So a term ending in
                # calendar year N also covers funding year N+1.
                if start_year <= year <= end_year + 1:
                    return True
            except (ValueError, IndexError):
                continue

        return False

    def get_bioguide_id(self, senator_name, year=None, state=None):
        """
        Get bioguide ID for a senator name.

        Args:
            senator_name: Name of senator (e.g., "LAMAR ALEXANDER")
            year: Funding year to match against senator terms (optional)
            state: Two-letter state code (optional, for disambiguation)

        Returns:
            Bioguide ID string or empty string if not found
        """
        if not senator_name:
            return ""

        normalized_input = self._normalize_name(senator_name)
        normalized_input = NAME_ALIASES.get(normalized_input, normalized_input)

        # Find matching senators
        matches = []
        for senator in self.senators:
            # Check if name matches
            senator_names = self._get_senator_full_name(senator)
            if normalized_input in senator_names:
                # Check if senator was active in the given year
                if self._is_senator_active(senator, year):
                    # If state is provided, check if it matches
                    if state:
                        state_match = any(
                            term.get('state', '').upper() == state.upper()
                            for term in senator['terms']
                        )
                        if state_match:
                            matches.append(senator)
                    else:
                        matches.append(senator)

        # Return the bioguide ID if we have exactly one match
        if len(matches) == 1:
            return matches[0]['bioguide_id']
        elif len(matches) > 1:
            # Multiple matches - log warning and return first match
            print(f"Warning: Multiple matches for '{senator_name}' in year {year}")
            return matches[0]['bioguide_id']
        else:
            # No matches found
            return ""

    def get_match_info(self, senator_name, year=None):
        """
        Get detailed match information for debugging.

        Returns a dictionary with match details.
        """
        if not senator_name:
            return {"error": "No name provided"}

        normalized_input = self._normalize_name(senator_name)
        normalized_input = NAME_ALIASES.get(normalized_input, normalized_input)

        matches = []
        for senator in self.senators:
            senator_names = self._get_senator_full_name(senator)
            if normalized_input in senator_names:
                if self._is_senator_active(senator, year):
                    matches.append({
                        'bioguide_id': senator['bioguide_id'],
                        'full_name': senator['official_full'],
                        'matched_name': normalized_input,
                        'terms': senator['terms']
                    })

        return {
            'input_name': senator_name,
            'normalized_name': normalized_input,
            'year': year,
            'matches': matches,
            'match_count': len(matches)
        }


def main():
    """Test the bioguide matcher."""
    matcher = BioguideIdMatcher()

    # Test cases
    test_cases = [
        ("LAMAR ALEXANDER", 2014),
        ("ELIZABETH WARREN", 2015),
        ("JOHN MCCAIN", 2014),
        ("BARACK OBAMA", 2008),  # Was senator before president
    ]

    print("\n=== Testing Bioguide Matcher ===")
    for name, year in test_cases:
        bioguide_id = matcher.get_bioguide_id(name, year)
        print(f"{name} ({year}): {bioguide_id}")

        # Show detailed info
        info = matcher.get_match_info(name, year)
        if info['matches']:
            print(f"  Full name: {info['matches'][0]['full_name']}")
        print()


if __name__ == '__main__':
    main()
