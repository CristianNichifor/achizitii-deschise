#!/usr/bin/env bash
# Fetch the published bundle into site/data, unless it is already there.
#
# The derived aggregates are no longer committed — see docs/storage.md. Both `pages.yml`
# (which deploys them) and `tests.yml` (which asserts things about them) need them present,
# and a fetch written twice is a fetch that drifts, so it lives here.
#
# Newest `bundle-*` release wins. `gh release list` is already newest-first, so a bundle
# published minutes ago is picked up without anyone editing a tag.
#
# Doing nothing when site/data/manifest.json already exists is deliberate: it keeps a local
# clone that has just run `achizitii publish` from having its fresh build overwritten by the
# last released one.
#
#   ./scripts/fetch_bundle.sh
#
# Needs the GitHub CLI. In Actions, set GH_TOKEN: ${{ github.token }}.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f site/data/manifest.json ]; then
  echo "site/data e deja prezent — nu descarc nimic"
  exit 0
fi

DEPOZIT=${GITHUB_REPOSITORY:-CristianNichifor/achizitii-deschise}

eticheta=$(gh release list --repo "$DEPOZIT" --limit 30 --json tagName \
             -q '[.[].tagName | select(startswith("bundle-"))] | first')

if [ -z "$eticheta" ] || [ "$eticheta" = "null" ]; then
  echo "nu găsesc niciun release bundle-*, iar site/data nu e în checkout." >&2
  echo "Rulează 'achizitii publish' și apoi 'scripts/release_bundle.sh'." >&2
  exit 1
fi

echo "descarc $eticheta"
tinta=$(mktemp -d)
trap 'rm -rf "$tinta"' EXIT

gh release download "$eticheta" --repo "$DEPOZIT" --pattern 'bundle-*.tar.gz' --dir "$tinta"
# The archive holds paths relative to site/, so this lands them back under site/data.
tar -xzf "$tinta"/bundle-*.tar.gz -C site

echo "site/data:"
du -sh site/data
