#!/usr/bin/env bash
# Fetch the published bundle into site/data, unless it is already there.
#
# The derived aggregates are no longer committed — see docs/storage.md. Three workflows need
# them present: `pages.yml` deploys them, `tests.yml` asserts things about them, and
# `browser.yml` serves site/ to a real browser that reads the Parquet over HTTP. A fetch
# written three times is a fetch that drifts, so it lives here.
#
# **curl and python3, not the GitHub CLI.** `browser.yml` runs inside the Playwright container
# image, which has no `gh`. python3 is already a hard dependency there — playwright.config.js
# serves the site with `python3 -m http.server` — so parsing the release JSON with it costs
# nothing and keeps this runnable in all three places.
#
# Newest `bundle-*` release wins, and the API returns releases newest-first, so a bundle
# published minutes ago is picked up without anyone editing a tag.
#
# Doing nothing when site/data/manifest.json already exists is deliberate: it keeps a local
# clone that has just run `achizitii publish` from having its fresh build overwritten by the
# last released one.
#
#   ./scripts/fetch_bundle.sh
#
# GITHUB_TOKEN or GH_TOKEN is used when set. The repository is public, so the download works
# without one; a token only raises the rate limit.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f site/data/manifest.json ]; then
  echo "site/data e deja prezent — nu descarc nimic"
  exit 0
fi

DEPOZIT=${GITHUB_REPOSITORY:-CristianNichifor/achizitii-deschise}
JETON=${GITHUB_TOKEN:-${GH_TOKEN:-}}

antet=()
[ -n "$JETON" ] && antet=(-H "Authorization: Bearer $JETON")

lista=$(curl -sSfL "${antet[@]}" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$DEPOZIT/releases?per_page=30")

# Prints "<tag> <url>" for the newest bundle-* release that actually carries the archive.
# A tag without its asset is not a usable release, so it is skipped rather than failed on.
citire=$(printf '%s' "$lista" | python3 -c '
import json, sys
for lansare in json.load(sys.stdin):
    if not lansare.get("tag_name", "").startswith("bundle-"):
        continue
    for fisier in lansare.get("assets", []):
        if fisier["name"].startswith("bundle-") and fisier["name"].endswith(".tar.gz"):
            print(lansare["tag_name"], fisier["browser_download_url"])
            sys.exit(0)
')

if [ -z "$citire" ]; then
  echo "nu găsesc niciun release bundle-* cu arhivă, iar site/data nu e în checkout." >&2
  echo "Rulează 'achizitii publish' și apoi 'scripts/release_bundle.sh'." >&2
  exit 1
fi

eticheta=${citire%% *}
adresa=${citire#* }

echo "descarc $eticheta"
tinta=$(mktemp -d)
trap 'rm -rf "$tinta"' EXIT

curl -sSfL "${antet[@]}" -o "$tinta/bundle.tar.gz" "$adresa"
# The archive holds paths relative to site/, so this lands them back under site/data.
tar -xzf "$tinta/bundle.tar.gz" -C site

[ -f site/data/manifest.json ] || { echo "arhiva nu conține data/manifest.json" >&2; exit 1; }

echo "site/data:"
du -sh site/data
