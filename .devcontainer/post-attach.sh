#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lukas Koerber
# SPDX-License-Identifier: MIT
#
# Prints the first steps every time a terminal attaches.
set -euo pipefail

if [ -n "${CODESPACE_NAME:-}" ]; then
  url="https://${CODESPACE_NAME}-8123.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
else
  url="http://localhost:8123"
fi

cat <<EOF

GitLab Monitor dev container

  Run the tests:
    pytest tests/ -v --cov=custom_components/gitlab_monitor --cov-report=json:coverage.json

  Start Home Assistant with this integration loaded from the checkout:
    scripts/develop
    then open ${url}

  The first start takes a few minutes (Home Assistant sets itself up and installs missing
  requirements into config/deps). Onboarding creates a local owner account; config/ is
  git-ignored and stays in this workspace only.

EOF