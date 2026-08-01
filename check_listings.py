#!/usr/bin/env python3
"""
Polls Boston.gov Metrolist's developments API and posts new listings
to a Discord channel via webhook. Keeps track of which listing IDs
have already been seen in seen_listings.json so it only alerts once
per listing.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

API_URL = "https://www.boston.gov/metrolist/api/v1/developments?_format=json"
SEEN_FILE = Path(__file__).parent / "seen_listings.json"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Each row in the API is one (development, offer-type) pairing, e.g. a
# building that has both a rental and sale listing shows up twice.
# "id" already encodes that (e.g. "16597446-s-l"), so it's a good
# unique key for "have I alerted on this specific listing before".


def load_seen_ids():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_ids(ids):
    SEEN_FILE.write_text(json.dumps(sorted(ids), indent=2))


def fetch_listings(max_retries=3):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(API_URL, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < max_retries:
                wait = 5 * attempt  # 5s, 10s backoff
                print(f"Fetch attempt {attempt} failed ({e}); retrying in {wait}s...")
                time.sleep(wait)
    raise last_error


def format_price(units):
    prices = [u["price"] for u in units if u.get("price")]
    if not prices:
        return "Price not listed"
    lo, hi = min(prices), max(prices)
    rate = units[0].get("priceRate")
    suffix = "/mo" if rate == "monthly" else ""
    if lo == hi:
        return f"${lo:,.0f}{suffix}"
    return f"${lo:,.0f}–${hi:,.0f}{suffix}"


def format_bedrooms(units):
    beds = sorted({u["bedrooms"] for u in units if u.get("bedrooms") is not None})
    if not beds:
        return "N/A"
    labeled = ["Studio" if b == 0 else f"{b}BR" for b in beds]
    return ", ".join(labeled)


def format_ami(units):
    # Metrolist's API doesn't expose a raw dollar "minimum income" figure
    # the way Maloney's site does -- it only exposes AMI% (Area Median
    # Income percentage), which is the qualification threshold. Surfacing
    # that, clearly labeled, rather than a dollar figure that isn't
    # actually available.
    amis = sorted({u["amiQualification"] for u in units if u.get("amiQualification") is not None})
    if not amis:
        return "N/A"
    if len(amis) == 1:
        return f"{amis[0]}%"
    return f"{amis[0]}%–{amis[-1]}%"


def build_discord_embed(listing):
    units = listing.get("units", [])
    city = listing.get("city", "")
    neighborhood = listing.get("neighborhood")
    location = f"{neighborhood}, {city}" if neighborhood else city
    offer = "For Rent" if listing.get("offer") == "rent" else "For Sale"
    slug = listing.get("slug", "")
    url = f"https://www.boston.gov/metrolist/search/housing/{slug}"

    return {
        "title": listing.get("title", "New Listing"),
        "url": url,
        "description": f"**{offer}** — {location}",
        "color": 3066993 if listing.get("offer") == "rent" else 15105570,
        "fields": [
            {"name": "Location", "value": location or "N/A", "inline": True},
            {"name": "Apartment Size", "value": format_bedrooms(units), "inline": True},
            {"name": "Income Limit (AMI %)", "value": format_ami(units), "inline": True},
            {"name": "Price", "value": format_price(units), "inline": True},
            {
                "name": "Application Due",
                "value": listing.get("applicationDueDate") or "Not specified",
                "inline": True,
            },
            {
                "name": "Address",
                "value": listing.get("streetAddress", "N/A"),
                "inline": False,
            },
        ],
    }


def post_to_discord(new_listings):
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    # Discord allows up to 10 embeds per message; batch if needed.
    batch_size = 10
    for i in range(0, len(new_listings), batch_size):
        batch = new_listings[i : i + batch_size]
        payload = {
            "content": f"@everyone 🏠 {len(batch)} new Metrolist listing(s)!"
            if i == 0
            else None,
            "embeds": [build_discord_embed(listing) for listing in batch],
            "allowed_mentions": {"parse": ["everyone"]},
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        resp.raise_for_status()
        time.sleep(1)  # be polite to Discord's rate limits between batches


def main():
    seen_ids = load_seen_ids()
    try:
        listings = fetch_listings()
    except requests.exceptions.RequestException as e:
        # Transient outage on Boston.gov's end (e.g. a 503). Skip this
        # pass rather than crashing -- the loop that calls this script
        # will just try again on the next iteration.
        print(f"Skipping this check: {e}")
        return

    current_ids = {listing["id"] for listing in listings}
    new_ids = current_ids - seen_ids

    is_first_run = len(seen_ids) == 0

    if new_ids:
        new_listings = [l for l in listings if l["id"] in new_ids]
        if is_first_run:
            # Don't blast Discord with every historical listing the
            # first time this ever runs — just record a baseline.
            print(f"First run: recording {len(new_ids)} existing listings as baseline, no alert sent.")
        else:
            print(f"Found {len(new_listings)} new listing(s). Posting to Discord...")
            post_to_discord(new_listings)
    else:
        print("No new listings.")

    save_seen_ids(current_ids)


if __name__ == "__main__":
    main()
