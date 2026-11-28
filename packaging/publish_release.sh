#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
#
# Attach built files to the GitHub Release for a version tag, creating it as a *draft*
# if it doesn't exist yet. Nothing is public until someone reviews the draft and
# publishes it.
#
#   packaging/publish_release.sh v1.0.1 dist/PyAmp-1.0.1-macos-arm64.dmg ...
#
# Run by the package workflows (macOS and Windows) on version tags, one after the other
# (they share a concurrency group): the first creates the draft, the next adds to it.
# Needs `gh` with GH_TOKEN allowed to write the repository's contents.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <tag> <file>..." >&2
  exit 2
fi
tag=$1
shift

# The files are named after the app's version (pyproject.toml / pyamp.__version__); a
# tag that doesn't match it would publish e.g. "v1.1.0" with 1.0.1 builds.
for file in "$@"; do
  case "$(basename "$file")" in
    *"-${tag#v}-"*) ;;
    *)
      echo "error: $file isn't version ${tag#v}; bump the version before tagging $tag" >&2
      exit 1
      ;;
  esac
done

# GitHub's "release by tag" lookup skips drafts (they aren't tied to the tag until
# published), so look in the list, drafts included.
exists=$(gh release list --limit 100 --json tagName --jq "map(select(.tagName == \"$tag\")) | length")
if [ "$exists" = "0" ]; then
  echo "Creating draft release $tag"
  gh release create "$tag" --draft --verify-tag --title "PyAmp ${tag#v}" --generate-notes
fi

echo "Attaching to $tag: $*"
gh release upload "$tag" "$@" --clobber
