# Affordable Housing Listing Bot

Checks two affordable housing sites for new listings and posts them to a
Discord channel:

- Boston.gov's [Metrolist](https://www.boston.gov/metrolist/search) — checked ~every 25 seconds
- [Maloney Affordable](https://www.maloneyaffordable.com/apartment-rentals/) apartment rentals — checked ~every 25 seconds (offset from Metrolist)

## How it works

**Metrolist** (`check_listings.py` / `check-listings.yml`)
- Boston.gov's search page pulls its data from a public JSON API:
  `https://www.boston.gov/metrolist/api/v1/developments?_format=json`
- The script fetches that API, compares listing IDs against
  `seen_listings.json`, and posts anything new to Discord.

**Maloney Affordable** (`check_maloney.py` / `check-maloney.yml`)
- This site doesn't expose a JSON API, so the script parses the
  server-rendered HTML table on the apartment-rentals page directly
  (via BeautifulSoup).
- A listing is identified by (property + unit size + Lottery/FCFS type).
  If the price or number-of-units-available changes on an existing
  listing, that alone won't trigger a new alert — only a genuinely new
  property/unit-size/type combination will.
- Compares against `seen_maloney_listings.json`.
- Note: Maloney also has its own native "sign up for new listing alerts"
  email form directly on the page, if you'd rather use that instead of
  or alongside this.

Both workflows run on a schedule via GitHub Actions and commit their
updated "seen" file back to the repo after each run, so state persists
between runs without needing a database or server.

## Setup

### 1. Create a Discord webhook

In the Discord channel you want alerts in:
Channel Settings → Integrations → Webhooks → New Webhook → copy the
**Webhook URL**.

### 2. Create a GitHub repo

Create a new repository. **This repo needs to be public** for the
polling loop described below to run on GitHub's free Actions minutes --
a private repo would burn through its monthly free-minutes allowance in
about a day and a half at this cadence. There's nothing sensitive in
this repo (just listing IDs and property data); the webhook URL stays
hidden as a secret either way.

```
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

### 3. Add the webhook URL as a repo secret

In your new repo on GitHub: **Settings → Secrets and variables → Actions →
New repository secret**

- Name: `DISCORD_WEBHOOK_URL`
- Value: the webhook URL from step 1

### 4. Enable Actions (if not already)

Go to the **Actions** tab in your repo — GitHub sometimes asks you to
confirm you want workflows enabled for a new repo. Click to enable.

### 5. First run

Go to the **Actions** tab and manually run **both** workflows once
("Check Metrolist Listings" and "Check Maloney Listings" → Run workflow).
Each first run just records everything currently on its site as the
"already seen" baseline — it won't flood your Discord with 80+ historical
listings. After that, both run automatically every 10 minutes and will
only post genuinely new listings.

## Adjusting the check frequency

Each workflow triggers on a 5-minute cron schedule (roughly the floor
GitHub Actions guarantees for scheduled triggers), but the job itself
loops the actual check every ~25 seconds for about 4.5 minutes before
exiting -- so real-world detection cadence is closer to 25-30 seconds,
not 5 minutes. The `seen_*.json` file is updated on disk every loop
pass but only committed to git once, at the end of the loop, to avoid
spamming commits or racing git pushes mid-loop.

To change the loop interval, edit the `sleep 25` line and the
`END=$((SECONDS + 270))` value (270 seconds = 4.5 minutes, leaving a
buffer before the next scheduled trigger) in either workflow file.
Going faster than ~15-20 seconds isn't recommended -- it doesn't buy
much and increases the chance of overlapping runs.

Each workflow also has `concurrency: cancel-in-progress: true` set, so
if a run ever takes longer than expected and the next scheduled trigger
fires before it's done, the older run gets cancelled rather than
running two loops (and two git pushes) at once.

GitHub also auto-disables scheduled workflows after 60 days with no
repository activity. Since both workflows commit back to the repo on
every run, that itself counts as activity, so this shouldn't be an
issue in practice -- but worth knowing if a schedule ever mysteriously
stops firing.

## Notes

- If you ever want to reset and re-baseline a given site (e.g. you
  accidentally missed some listings and want to start fresh), delete
  the contents of that site's "seen" file (`seen_listings.json` or
  `seen_maloney_listings.json`) and replace it with `[]`, commit, and
  the next run will treat it as a first run again.
- Both scripts are read-only against their target sites — just GET
  requests, no login involved.
- The Metrolist checker reads a JSON API, so it should keep working
  unless Boston.gov changes that API's shape.
- The Maloney checker parses an HTML table, which is inherently more
  fragile — if Maloney redesigns that page, the scraper may need
  updating. If it ever starts erroring, check the Actions tab logs
  first; the script raises a clear error if it can't find a table or
  parses zero rows, rather than silently reporting nothing.
