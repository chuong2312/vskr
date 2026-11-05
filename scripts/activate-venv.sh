#!/usr/bin/env bash
# Run with: source scripts/activate-venv.sh

VSKR_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "${VSKR_ROOT}/.venv/bin/activate" ]]; then
  printf 'Không tìm thấy venv tại %s/.venv; tạo venv trước.\n' "${VSKR_ROOT}" >&2
  return 1
fi
source "${VSKR_ROOT}/.venv/bin/activate"

export PYTHONPATH="${VSKR_ROOT}/backend/src${PYTHONPATH:+:${PYTHONPATH}}"
export HF_HOME="${HF_HOME:-${VSKR_ROOT}/cache/huggingface}"
export LD_LIBRARY_PATH="${VSKR_ROOT}/.venv/lib/python3.12/site-packages/nvidia/cublas/lib:${VSKR_ROOT}/.venv/lib/python3.12/site-packages/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

printf 'Đã kích hoạt VSKR venv: %s\n' "${VSKR_ROOT}/.venv"
printf 'Python: %s\n' "$(python --version)"
unset VSKR_ROOT
