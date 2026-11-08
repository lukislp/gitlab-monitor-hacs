#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Lukas Koerber
# SPDX-License-Identifier: MIT
#
# Installs the pinned test and lint requirements the way CI does (hash-checked). Runs in the
# Codespaces prebuild (updateContentCommand) and again on open when the prebuild is older than
# the latest commit.
set -euo pipefail

cd "$(dirname "$0")/.."

pip install --require-hashes -r requirements_test.txt -r requirements_lint.txt