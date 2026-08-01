#!/usr/bin/env python3
"""
Scrapes the "Current Rental Availability" table on
https://www.maloneyaffordable.com/apartment-rentals/ and posts new rows
to a Discord channel via webhook. Keeps track of which rows have already
been seen in seen_maloney_listings.json so it only alerts once per
listing.

Unlike Metrolist, this site doesn't expose a JSON API -- the table is
plain server-rendered HTML, so this uses BeautifulSoup to parse it.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.maloneyaffordable.com/apartment-rentals/"
SEEN_FILE = Path(__file__).parent / "seen_maloney_listings.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Column order as they currently appear in the table on the page.
COLUMNS = [
    "property",
    "town",
    "unit_size",
    "price",
    "min_income",
    "ami_percent",
    "type",
    "units_available",
    "accessible_units",
    "learn_more",
]


def load_seen_ids():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_ids(ids):
    SEEN_FILE.write_text(json.dumps(sorted(ids), indent=2))


def fetch_page_html(max_retries=3):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                URL, timeout=30, headers={"User-Agent": "Mozilla/5.0 (listing-alert-bot)"}
            )
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < max_retries:
                wait = 5 * attempt  # 5s, 10s backoff
                print(f"Fetch attempt {attempt} failed ({e}); retrying in {wait}s...")
                time.sleep(wait)
    raise last_error


def fetch_rows():
    html = fetch_page_html()
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table")
    if table is None:
        raise RuntimeError(
            "Couldn't find a <table> on the page -- the site's layout may have changed."
        )

    tbody = table.find("tbody") or table
    rows = []
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        row = {}
        property_url = None
        for col_name, cell in zip(COLUMNS, cells):
            row[col_name] = cell.get_text(strip=True)
            a = cell.find("a")
            if a and a.get("href") and property_url is None:
                property_url = a["href"]
        row["property_url"] = property_url or ""
        rows.append(row)

    if not rows:
        raise RuntimeError(
            "Found a table but parsed zero rows -- the site's layout may have changed."
        )

    return rows


def row_id(row):
    # A listing is identified by which property + unit size + lottery/FCFS
    # type it is. Price/income/unit-count changes on an existing listing
    # won't re-trigger an alert -- only a genuinely new (property, unit
    # size, type) combination will.
    key = f"{row['property_url']}|{row['unit_size']}|{row['type']}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def build_discord_embed(row):
    return {
        "title": row["property"] or "New Listing",
        "url": row["property_url"] or URL,
        "description": f"**{row['type']}** — {row['town']}",
        "color": 3066993 if row["type"].upper() == "FCFS" else 15105570,
        "fields": [
            {"name": "Location", "value": row["town"] or "N/A", "inline": True},
            {"name": "Apartment Size", "value": row["unit_size"] or "N/A", "inline": True},
            {
                "name": "Minimum Income",
                "value": row["min_income"] or "N/A",
                "inline": True,
            },
            {"name": "Monthly Price", "value": row["price"] or "N/A", "inline": True},
            {
                "name": "Units Available",
                "value": row["units_available"] or "N/A",
                "inline": True,
            },
            {
                "name": "AMI %",
                "value": row["ami_percent"] or "N/A",
                "inline": True,
            },
            {
                "name": "Accessible Units",
                "value": row["accessible_units"] or "0",
                "inline": False,
            },
        ],
    }


def post_to_discord(new_rows):
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    batch_size = 10
    for i in range(0, len(new_rows), batch_size):
        batch = new_rows[i : i + batch_size]
        payload = {
            "content": f"@everyone 🏠 {len(batch)} new Maloney listing(s)!" if i == 0 else None,
            "embeds": [build_discord_embed(row) for row in batch],
            "allowed_mentions": {"parse": ["everyone"]},
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        resp.raise_for_status()
        time.sleep(1)


def main():
    seen_ids = load_seen_ids()
    try:
        rows = fetch_rows()
    except requests.exceptions.RequestException as e:
        # Transient outage on Maloney's end. Skip this pass rather than
        # crashing -- the loop that calls this script will just try
        # again on the next iteration.
        print(f"Skipping this check: {e}")
        return

    current_ids = {row_id(r) for r in rows}
    id_to_row = {row_id(r): r for r in rows}
    new_ids = current_ids - seen_ids

    is_first_run = len(seen_ids) == 0

    if new_ids:
        new_rows = [id_to_row[i] for i in new_ids]
        if is_first_run:
            print(
                f"First run: recording {len(new_ids)} existing listings as baseline, no alert sent."
            )
        else:
            print(f"Found {len(new_rows)} new listing(s). Posting to Discord...")
            post_to_discord(new_rows)
    else:
        print("No new listings.")

    save_seen_ids(current_ids)


if __name__ == "__main__":
    main()
