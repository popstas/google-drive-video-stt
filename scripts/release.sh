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

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "error: tag ${TAG} already exists; run 'git checkout -- .' to undo the bump" >&2
  exit 1
fi

echo "==> Regenerating CHANGELOG.md for $TAG"
uv run git-cliff --tag "$TAG" -o CHANGELOG.md

if ! grep -q "^## ${TAG} " CHANGELOG.md; then
  echo "error: CHANGELOG.md has no '## ${TAG}' heading after regeneration" >&2
  exit 1
fi

echo "==> Committing and tagging"
# uv.lock records this project's own version, and the uv run calls above have
# already re-synced it. Stage it too, or every release leaves the tree dirty.
git add pyproject.toml CHANGELOG.md uv.lock
# Skip the generate-changelog hook: it runs git-cliff WITHOUT --tag, which would
# rewrite the heading we just produced back to "Unreleased", modify the staged
# file and abort the commit. The changelog for this commit is already correct.
SKIP=generate-changelog git commit -m "chore: release ${TAG}"
git tag "$TAG"

echo "==> Done. Review, then run:"
echo "    git push && git push --tags"
