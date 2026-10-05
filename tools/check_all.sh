#!/usr/bin/env bash
# Run every local check (CLAUDE.md, Git and checks). Exits non-zero if any check fails.
# Usage (repo root):  ./tools/check_all.sh [--phase N] [--ci]
#   --phase N : last phase built; the trace report fails for untested TRs of phases <= N
#   --ci      : skip the trace report (it reads git-ignored docs that CI does not have)
set -u

PHASE=0
CI_MODE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --ci) CI_MODE=1; shift ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2

if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
else
  PY="python"
fi

NAMES=()
CODES=()
run() {
  local name="$1"; shift
  echo ""
  echo "==> $name"
  "$@"
  local code=$?
  NAMES+=("$name")
  CODES+=("$code")
}

run "pytest (incl. PHI leak tests)" "$PY" -m pytest
run "file size" "$PY" tools/check_file_size.py
run "layer imports" "$PY" tools/check_layers.py
run "ruff lint" "$PY" -m ruff check .
run "ruff format" "$PY" -m ruff format --check .
run "mypy" "$PY" -m mypy
run "pylint max-module-lines" "$PY" -m pylint --disable=all --enable=too-many-lines \
  --max-module-lines=1000 --score=n app tools tests
run "secret scan" "$PY" tools/check_secrets.py
run "synthetic markers" "$PY" tools/check_synthetic_markers.py
run "web lint" npm --prefix web run lint
run "web tests" npm --prefix web test
run "web build" npm --prefix web run build
if [ "$CI_MODE" -eq 1 ]; then
  echo ""
  echo "==> trace report: skipped in CI (reads git-ignored docs)"
else
  run "trace report (phase $PHASE)" "$PY" tools/trace_report.py --phase "$PHASE"
fi

echo ""
echo "==== Summary ===="
FAILED=0
for i in "${!NAMES[@]}"; do
  if [ "${CODES[$i]}" -eq 0 ]; then
    echo "PASS  ${NAMES[$i]}"
  else
    echo "FAIL  ${NAMES[$i]}"
    FAILED=$((FAILED + 1))
  fi
done
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED check(s) failed."
  exit 1
fi
echo "All checks passed."
exit 0
