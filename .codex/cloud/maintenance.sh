#!/usr/bin/env bash
set -euo pipefail

MINIFORGE_ROOT="${HOME}/miniforge3"
ENV_NAME="fypr-reaktoro"
CONDA="${MINIFORGE_ROOT}/bin/conda"

if [[ ! -x "${CONDA}" ]]; then
    echo "Miniforge is missing; rerun the Codex Cloud setup script." >&2
    exit 1
fi

"${CONDA}" env update -n "${ENV_NAME}" -f environment.yml --prune

"${CONDA}" run -n "${ENV_NAME}" python - <<'PY'
import importlib.metadata
import sys
import reaktoro

print(f"Python: {sys.version.split()[0]}")
print(f"Reaktoro: {importlib.metadata.version('reaktoro')}")
print(f"Reaktoro module: {reaktoro.__file__}")
PY
