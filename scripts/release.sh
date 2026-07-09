#!/usr/bin/env bash
#
# Cut a release: bump the version, regenerate the changelog, commit, and tag.
#
# Version lives only in pyproject.toml ([tool.bumpversion] there drives the
# edit; commit/tag are owned here so CHANGELOG.md lands in the same commit).
# Pushing the resulting tag triggers .github/workflows/release.yml, which
# publishes a GitHub Release with git-cliff notes.
#
# Usage:
#   scripts/release.sh [major|minor|patch]   # default: patch
#
# Then review and push:
#   git push && git push --tags
set -euo pipefail

PART="${1:-patch}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is dirty; commit or stash first" >&2
  exit 1
fi

echo "==> Bumping version ($PART)"
uv run bump-my-version bump "$PART"

NEW_VERSION="$(uv run bump-my-version show current_version)"
TAG="v${NEW_VERSION}"
echo "==> New version: $NEW_VERSION"

echo "==> Regenerating CHANGELOG.md for $TAG"
uv run git-cliff --tag "$TAG" -o CHANGELOG.md

echo "==> Committing and tagging"
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release ${TAG}"
git tag "$TAG"

echo "==> Done. Review, then run:"
echo "    git push && git push --tags"
