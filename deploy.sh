#!/usr/bin/env bash
# Deploy update for MY-SHOP on any Linux server (cPanel/Passenger, VPS, plain venv).
#   ./deploy.sh            (or: bash deploy.sh)
# Steps: pull latest code -> install requirements -> migrate -> collectstatic -> reload.
# git pull alone does NOT refresh the JS/CSS browsers receive (WhiteNoise serves
# the collected staticfiles/), so always deploy with this script.
#
# Optional settings (environment variables):
#   DEPLOY_BRANCH=main      branch to deploy
#   DEPLOY_PYTHON=/path     Python to use (otherwise auto-detected)
#   DEPLOY_SKIP_PIP=1       skip "pip install -r requirements.txt"
#   DEPLOY_SERVICE=name     systemd service to restart (gunicorn/VPS installs)
set -euo pipefail

die() {
  echo "" >&2
  echo "Deploy stopped: $*" >&2
  exit 1
}

find_python() {
  if [ -n "${DEPLOY_PYTHON:-}" ]; then
    echo "$DEPLOY_PYTHON"
    return
  fi
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    echo "$VIRTUAL_ENV/bin/python"
    return
  fi
  local d
  for d in .venv venv env; do
    if [ -x "$d/bin/python" ]; then
      echo "$PWD/$d/bin/python"
      return
    fi
  done
  # cPanel "Setup Python App": ~/virtualenv/<app folder relative to home>/<version>/
  local project home rel cand
  project="$(pwd -P)"
  home="$(cd "$HOME" 2>/dev/null && pwd -P || echo "$HOME")"
  rel="${project#"$home"/}"
  if [ "$rel" != "$project" ]; then
    cand="$(ls -d "$home/virtualenv/$rel"/*/bin/python 2>/dev/null | sort -V | tail -n 1 || true)"
    if [ -n "$cand" ]; then
      echo "$cand"
      return
    fi
  fi
  command -v python3 || command -v python || die "no Python found. Set DEPLOY_PYTHON=/path/to/python."
}

update_code() {
  local branch="$1"
  echo "[1/4] Pulling latest code ($branch)..."
  git fetch origin "+refs/heads/$branch:refs/remotes/origin/$branch" || die "could not download from GitHub (check internet/credentials)."

  if ! git diff --quiet || ! git diff --cached --quiet; then
    git status --short --untracked-files=no >&2
    die "the files above were edited on this server. Undo them (git checkout -- <file>) or commit them, then re-run."
  fi

  if [ "$(git rev-parse --abbrev-ref HEAD)" != "$branch" ]; then
    git checkout "$branch" || die "could not switch to branch $branch."
  fi

  git merge --ff-only "origin/$branch" \
    || die "this server copy has its own commits that are not on GitHub. Ask a developer to sort it out."
}

main() {
  cd "$(dirname "$0")"
  local branch="${DEPLOY_BRANCH:-main}"

  if [ "${MYSHOP_DEPLOY_PULLED:-}" != "1" ]; then
    update_code "$branch"
    # Re-run the freshly pulled version of this script for the remaining steps.
    export MYSHOP_DEPLOY_PULLED=1
    exec bash "$PWD/$(basename "$0")" "$@"
  fi

  local py
  py="$(find_python)"
  echo "Using Python: $py ($("$py" --version 2>&1))"

  if [ "${DEPLOY_SKIP_PIP:-}" = "1" ]; then
    echo "[2/4] Skipping requirements (DEPLOY_SKIP_PIP=1)."
  else
    echo "[2/4] Installing requirements..."
    "$py" -m pip install --disable-pip-version-check -q -r requirements.txt \
      || die "pip install failed. See messages above."
  fi

  echo "[3/4] Applying database migrations..."
  "$py" manage.py migrate --noinput || die "migrations failed. See messages above."

  echo "[4/4] Rebuilding static files..."
  "$py" manage.py collectstatic --noinput || die "collectstatic failed. See messages above."

  # Passenger (cPanel) reloads the app on the next request; harmless elsewhere.
  mkdir -p tmp && touch tmp/restart.txt

  if [ -n "${DEPLOY_SERVICE:-}" ]; then
    echo "Restarting service $DEPLOY_SERVICE..."
    sudo systemctl restart "$DEPLOY_SERVICE" || die "could not restart $DEPLOY_SERVICE."
  fi

  echo ""
  echo "Deploy complete: now at $(git log -1 --format='%h %s')"
  if [ -z "${DEPLOY_SERVICE:-}" ]; then
    echo "(Passenger/cPanel reloads automatically. Gunicorn/VPS: restart the service,"
    echo " or next time run DEPLOY_SERVICE=<name> ./deploy.sh.)"
  fi
}

# Wrapped in main so bash parses the whole file before git pull can change it.
main "$@"
exit
