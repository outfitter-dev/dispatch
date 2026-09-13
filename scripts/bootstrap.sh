#!/bin/sh
# Prepare this source checkout without installing global tools or starting services.
set -eu

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'bootstrap.sh does not accept arguments.' >&2
    exit 2
fi
if [ -L "$0" ]; then
    printf '%s\n' 'Refusing a script symlink; run the bootstrap from its owning checkout.' >&2
    exit 1
fi

unset CDPATH GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
unset VIRTUAL_ENV UV_PROJECT_ENVIRONMENT UV_WORKING_DIR UV_WORKING_DIRECTORY
unset UV_PROJECT UV_PYTHON UV_FROZEN PYTHONHOME PYTHONPATH
unset UV_NO_DEV UV_NO_GROUP UV_NO_EDITABLE
unset UV_NO_INSTALL_PROJECT UV_NO_INSTALL_WORKSPACE UV_NO_INSTALL_LOCAL UV_NO_INSTALL_PACKAGE
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'uv is required on PATH; install it explicitly, then rerun bootstrap.' >&2
    exit 127
fi

repo_root=$(cd -P "$(dirname "$0")/.." && pwd)
cd "$repo_root"
for required_file in pyproject.toml uv.lock; do
    if [ ! -f "$required_file" ]; then
        printf '%s\n' "Bootstrap requires $repo_root/$required_file." >&2
        exit 1
    fi
done
if [ -L .venv ]; then
    printf '%s\n' 'Refusing symlinked .venv; bootstrap requires a checkout-local environment.' >&2
    exit 1
fi
export UV_PROJECT_ENVIRONMENT="$repo_root/.venv"
exec uv sync --locked --group dev --directory "$repo_root" --project "$repo_root"
