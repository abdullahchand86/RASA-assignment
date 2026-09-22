#!/usr/bin/env python3
"""
Parse a Climatiq /search API response and print it as a clean table.

Usage:
    1. Save the raw JSON response to a file, e.g. response.json
       (curl ... > response.json)
    2. Run: python3 parse_climatiq.py response.json
"""

import json
import sys


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_table(results):
    if not results:
        print("No results found.")
        return

    # Columns to show
    columns = ["activity_id", "name", "category", "source", "year", "region", "unit", "factor"]
    headers = ["Activity ID", "Name", "Category", "Source", "Year", "Region", "Unit", "Factor"]

    # Compute column widths
    rows = []
    for r in results:
        row = [str(r.get(c, "")) for c in columns]
        rows.append(row)

    # Truncate long activity_id / name for readability
    max_widths = [40, 35, 22, 8, 6, 6, 16, 10]
    for row in rows:
        for i, w in enumerate(max_widths):
            if len(row[i]) > w:
                row[i] = row[i][: w - 3] + "..."

    # Print header
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, max_widths))
    print(header_line)
    print("-" * len(header_line))

    # Print rows
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
            # e.g. source or region -> list of dicts
            summarized = [v.get("source") or v.get("id") or str(v) for v in value]
            print(f"{key}: {', '.join(summarized)}")
        else:
            print(f"{key}: {value}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_climatiq.py <response.json>")
        sys.exit(1)

    data = load_json(sys.argv[1])
    results = data.get("results", [])

    print_table(results)
    print_possible_filters(data)


if __name__ == "__main__":
    main()
