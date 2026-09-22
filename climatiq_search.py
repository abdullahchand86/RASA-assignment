#!/usr/bin/env python3
"""
Standalone Climatiq /search tool.

Calls the Climatiq API directly (no need to run curl separately) and
prints the results as a clean table, plus the available filters for
that query.

USAGE
-----
Set your API key as an environment variable (recommended, keeps it out
of your shell history):

    export CLIMATIQ_API_KEY="your_key_here"

Then run with whatever search parameters you want:

    python3 climatiq_search.py --query electricity --region DE
    python3 climatiq_search.py --query flight --region DE --sector Transport
    python3 climatiq_search.py --query electricity --region DE --category Electricity --year 2023

Or pass the key directly (less safe, shows up in shell history):

    python3 climatiq_search.py --api-key YOUR_KEY --query electricity --region DE

To save the raw JSON response too:

    python3 climatiq_search.py --query electricity --region DE --save-json out.json

INPUTS
------
--api-key       Climatiq API key (falls back to CLIMATIQ_API_KEY env var)
--query         Free-text search term, e.g. "electricity", "flight"
--region        ISO country code, e.g. "DE", "US", "GB"
--sector        e.g. "Energy", "Transport"
--category      e.g. "Electricity", "Air Travel"
--source        e.g. "UBA", "EEA", "AIB"
--year          e.g. 2023
--data-version  Climatiq data version (default: "^6")
--save-json     Optional path to also save the raw JSON response

OUTPUT
------
Prints a formatted table of matching emission factors to stdout,
followed by a summary of available filters for that query.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error


API_BASE = "https://api.climatiq.io/data/v1/search"


def build_url(args):
    params = {"data_version": args.data_version, "query": args.query}
    if args.region:
        params["region"] = args.region
    if args.sector:
        params["sector"] = args.sector
    if args.category:
        params["category"] = args.category
    if args.source:
        params["source"] = args.source
    if args.year:
        params["year"] = args.year

    return f"{API_BASE}?{urllib.parse.urlencode(params)}"


def call_api(url, api_key):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"HTTP {e.code} error from Climatiq API:\n{body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error reaching Climatiq API: {e.reason}", file=sys.stderr)
        sys.exit(1)


def print_table(results):
    if not results:
        print("No results found.")
        return

    columns = ["activity_id", "name", "category", "source", "year", "region", "unit", "factor"]
    headers = ["Activity ID", "Name", "Category", "Source", "Year", "Region", "Unit", "Factor"]
    max_widths = [40, 35, 22, 8, 6, 6, 16, 10]

    rows = []
    for r in results:
        row = [str(r.get(c, "")) for c in columns]
        for i, w in enumerate(max_widths):
            if len(row[i]) > w:
                row[i] = row[i][: w - 3] + "..."
        rows.append(row)

    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, max_widths))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print(" | ".join(cell.ljust(w) for cell, w in zip(row, max_widths)))
    print(f"\nTotal results: {len(results)}")


def print_possible_filters(data):
    filters = data.get("possible_filters", {})
    if not filters:
        return
    print("\n--- Available Filters for This Query ---")
    for key, value in filters.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            summarized = [v.get("source") or v.get("id") or str(v) for v in value]
            print(f"{key}: {', '.join(summarized)}")
        else:
            print(f"{key}: {value}")


def parse_args():
    p = argparse.ArgumentParser(description="Search the Climatiq emission factor database.")
    p.add_argument("--api-key", default=os.environ.get("CLIMATIQ_API_KEY"),
                    help="Climatiq API key (or set CLIMATIQ_API_KEY env var)")
    p.add_argument("--query", required=True, help="Search term, e.g. 'electricity'")
    p.add_argument("--region", help="ISO country code, e.g. 'DE'")
    p.add_argument("--sector", help="e.g. 'Energy', 'Transport'")
    p.add_argument("--category", help="e.g. 'Electricity', 'Air Travel'")
    p.add_argument("--source", help="e.g. 'UBA', 'EEA'")
    p.add_argument("--year", type=int, help="e.g. 2023")
    p.add_argument("--data-version", default="^6", help="Climatiq data version (default: ^6)")
    p.add_argument("--save-json", help="Optional file path to save the raw JSON response")
    return p.parse_args()


def main():
    args = parse_args()

    if not args.api_key:
        print("Error: no API key provided. Use --api-key or set CLIMATIQ_API_KEY.", file=sys.stderr)
        sys.exit(1)

    url = build_url(args)
    print(f"Calling: {url}\n")

    data = call_api(url, args.api_key)

    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Raw JSON saved to {args.save_json}\n")

    print_table(data.get("results", []))
    print_possible_filters(data)


if __name__ == "__main__":
    main()
