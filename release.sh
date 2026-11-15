#!/usr/bin/env bash
# Release MY-SHOP: merge a feature branch into main and push.
#   bash release.sh                 merge the CURRENT branch into main
#   bash release.sh <branch>        merge <branch> into main
#
# Always merges origin/<branch> (the remote ref), never the local branch —
# a stale local checkout cannot be released by accident. After the merge,
# main is pushed and you are switched back to the feature branch.
# Then deploy on the server with: bash deploy.sh
set -euo pipefail

die() {
  echo "" >&2
  echo "Release stopped: $*" >&2
  exit 1
}

cd "$(dirname "$0")"

source_branch="${1:-$(git rev-parse --abbrev-ref HEAD)}"
[ "$source_branch" != "main" ] || die "already on main — pass the feature branch name: bash release.sh <branch>."

echo "[1/4] Fetching latest from GitHub..."
git fetch origin "+refs/heads/main:refs/remotes/origin/main" \
  "+refs/heads/$source_branch:refs/remotes/origin/$source_branch" \
  || die "could not download from GitHub (check internet/credentials)."

git show-ref --verify --quiet "refs/remotes/origin/$source_branch" \
  || die "branch '$source_branch' does not exist on GitHub. Push it first."

if ! git diff --quiet || ! git diff --cached --quiet; then
  git status --short --untracked-files=no >&2
  die "the files above have uncommitted changes. Commit or undo them first."
fi

echo "[2/4] Updating main..."
if git show-ref --verify --quiet "refs/heads/main"; then
  git checkout main || die "could not switch to main."
else
  git checkout -b main origin/main || die "could not switch to main."
fi
git merge --ff-only origin/main || die "local main has commits not on GitHub. Sort that out first."

# Merge the remote ref so a stale local branch can never be released.
source_ref="origin/$source_branch"
if git merge-base --is-ancestor "$source_ref" HEAD; then
  echo "main already contains $source_ref — nothing to merge."
else
  echo "Merging $source_ref ($(git rev-parse --short "$source_ref"))..."
  git merge --no-edit "$source_ref" || die "merge conflicts — resolve them, commit, and push main manually."
fi

echo "[3/4] Pushing main..."
git push origin main || die "could not push main to GitHub."

echo "[4/4] Back on $source_branch."
git checkout "$source_branch" || true

echo ""
echo "Released: main is now at $(git log -1 --format='%h %s' origin/main)"
echo "Deploy on the server with: bash deploy.sh"
