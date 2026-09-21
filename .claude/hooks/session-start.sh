#!/bin/bash
# Installs the package with its development extras so ruff and pytest work straight
# away in a Claude Code on the web session. Local machines have their own setup, so
# this is a no-op there.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# The editable install the README and CI use: the container image is cached after
# this runs, and pip is a no-op when everything is already current.
pip install -e ".[dev]"
