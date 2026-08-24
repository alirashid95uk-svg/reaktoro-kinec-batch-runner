#!/usr/bin/env bash
set -euo pipefail

MINIFORGE_ROOT="${HOME}/miniforge3"
ENV_NAME="fypr-reaktoro"

if [[ ! -x "${MINIFORGE_ROOT}/bin/conda" ]]; then
    arch="$(uname -m)"
    case "${arch}" in
        x86_64|aarch64) ;;
        *)
            echo "Unsupported architecture for Miniforge: ${arch}" >&2
            exit 1
            ;;
    esac

    installer="/tmp/Miniforge3-Linux-${arch}.sh"
    curl -fsSL \
        "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-${arch}.sh" \
        -o "${installer}"
    bash "${installer}" -b -p "${MINIFORGE_ROOT}"
fi

CONDA="${MINIFORGE_ROOT}/bin/conda"

# Keep `conda run ...` available even when Codex launches a non-interactive shell.
mkdir -p "${HOME}/.local/bin"
ln -sf "${CONDA}" "${HOME}/.local/bin/conda"

if "${CONDA}" env list | awk '{print $1}' | grep -Fxq "${ENV_NAME}"; then
    "${CONDA}" env update -n "${ENV_NAME}" -f environment.yml --prune
else
    "${CONDA}" env create -n "${ENV_NAME}" -f environment.yml
fi

# Also initialise interactive bash shells for manual debugging in a cloud chat.
if ! grep -Fq '# Codex Cloud: fypr-reaktoro' "${HOME}/.bashrc" 2>/dev/null; then
    cat >> "${HOME}/.bashrc" <<'EOF'

# Codex Cloud: fypr-reaktoro
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate fypr-reaktoro
EOF
fi

"${CONDA}" run -n "${ENV_NAME}" python - <<'PY'
import importlib.metadata
import sys
import reaktoro

print(f"Python: {sys.version.split()[0]}")
print(f"Reaktoro: {importlib.metadata.version('reaktoro')}")
print(f"Reaktoro module: {reaktoro.__file__}")
PY
