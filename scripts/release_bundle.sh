#!/usr/bin/env bash
# Publish the derived bundle as a release asset, so it stops being committed.
#
# Run after `achizitii publish`, from a clone that has the built site/data. The Pages
# workflow fetches the newest `bundle-*` release when site/data is not in the checkout,
# so this is what makes a deploy possible once the files leave git.
#
# Why a release and not R2: `docs/storage.md` keeps the aggregates on Pages on purpose —
# the common case, someone opening the site and reading totals, should never depend on an
# external service or spend a Class B operation. A release asset preserves that. It only
# changes where the Pages build gets the bytes from, not where a reader does.
#
# Everything under site/data is included EXCEPT site/data/preturi/**. Those are the
# collected daily prices: written once, never rewritten, and the only copy that exists —
# unit prices cannot be reconstructed retroactively because the bulk exports carry no
# quantities. They stay in git deliberately. See scripts/check_repo_size.py.
#
#   ./scripts/release_bundle.sh              # tag bundle-<today>
#   TAG=bundle-2026-09-10 ./scripts/release_bundle.sh
#
# Needs the GitHub CLI, authenticated with a token that can create releases.

set -euo pipefail

cd "$(dirname "$0")/.."

TAG=${TAG:-bundle-$(date +%F)}
ARHIVA="bundle-${TAG#bundle-}.tar.gz"

[ -f site/data/manifest.json ] || {
  echo "nu găsesc site/data/manifest.json — rulează întâi 'achizitii publish'" >&2
  exit 2
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Paths relative to site/, so the workflow can extract with `-C site` and land them back
# exactly where publish wrote them.
tar -czf "$tmp/$ARHIVA" -C site --exclude='data/preturi' data

marime=$(du -h "$tmp/$ARHIVA" | cut -f1)
echo "$ARHIVA: $marime"

# The manifest is uploaded beside the archive, uncompressed. It is small, and it lets
# anyone see what a bundle contains without downloading and unpacking the whole thing.
cp site/data/manifest.json "$tmp/manifest.json"

if gh release view "$TAG" >/dev/null 2>&1; then
  echo "release $TAG există — înlocuiesc fișierele"
  gh release upload "$TAG" "$tmp/$ARHIVA" "$tmp/manifest.json" --clobber
else
  gh release create "$TAG" "$tmp/$ARHIVA" "$tmp/manifest.json" \
    --title "Bundle ${TAG#bundle-}" \
    --notes "Agregatele derivate servite de pe GitHub Pages, construite cu \`achizitii publish\`.

Arhiva conține \`data/\` relativ la \`site/\`, fără \`data/preturi/\` — prețurile zilnice
colectate rămân în git, fiind singura copie care există.

Fluxul \`pages\` descarcă automat cel mai recent \`bundle-*\` când \`site/data\` nu e în checkout."
fi

echo
echo "gata. deploy-ul Pages va folosi $TAG când site/data nu e în checkout."
