#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! read -r -s -p "Admin password: " ADMIN_PASSWORD; then
    printf '\nFailed to read admin password.\n' >&2
    exit 1
fi
printf '\n'

if [[ -z "$ADMIN_PASSWORD" ]]; then
    printf 'Admin password must not be empty.\n' >&2
    exit 1
fi

if ! read -r -s -p "General user password: " USER_PASSWORD; then
    printf '\nFailed to read general user password.\n' >&2
    exit 1
fi
printf '\n'

if [[ -z "$USER_PASSWORD" ]]; then
    printf 'General user password must not be empty.\n' >&2
    exit 1
fi

export ADMIN_PASSWORD USER_PASSWORD
exec docker compose up --build --force-recreate "$@"
