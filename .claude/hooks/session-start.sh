#!/bin/bash
# Installs the package with its development extras so ruff and pytest work straight
# away in a Claude Code on the web session. Local machines have their own setup, so
# this is a no-op there.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Into .venv, the environment the README sets up, never through the pip on PATH: a
# Debian or Ubuntu Python 3.12 is marked externally managed (PEP 668) and refuses a
# bare `pip install`, and under `set -e` that ends the hook with no ruff and no
# pytest. The container image is cached after this runs; the editable install is
# cheap but not a no-op (it rebuilds the editable wheel every run), which is why an
# existing .venv is reused rather than recreated.
if [ ! -x .venv/bin/pip ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -e ".[dev]"

# The venv's ruff, pytest and randostats first on PATH for the rest of the session.
echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
