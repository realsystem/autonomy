#!/usr/bin/env bash
# Compare spec/schemas/v0 with the newest earlier spec-v0.* tag.
# Needs full history and tags (actions/checkout with fetch-depth: 0).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

prev=$(git tag --list 'spec-v0.*' --sort=-v:refname --no-contains HEAD | head -n1)
if [[ -z "$prev" ]]; then
  echo "no earlier spec-v0.* tag: nothing to compare (first release)"
  exit 0
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git archive "$prev" spec/schemas/v0 | tar -x -C "$tmp"
echo "comparing spec/schemas/v0 with $prev"
autonomy-conformance compat --old "$tmp/spec/schemas/v0" --new spec/schemas/v0 --verbose
