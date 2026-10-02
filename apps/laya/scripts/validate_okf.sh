#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KNOWLEDGE_DIR="${REPO_ROOT}/knowledge"

echo "=== Validating OKF v0.2 Knowledge Bundle in ${KNOWLEDGE_DIR} ==="

if [ ! -d "${KNOWLEDGE_DIR}" ]; then
  echo "❌ Error: knowledge directory not found: ${KNOWLEDGE_DIR}"
  exit 1
fi

if [ ! -f "${KNOWLEDGE_DIR}/index.md" ]; then
  echo "❌ Error: Root knowledge/index.md missing"
  exit 1
fi

if ! grep -q 'okf_version: "0.2"' "${KNOWLEDGE_DIR}/index.md"; then
  echo "❌ Error: knowledge/index.md missing okf_version: \"0.2\" declaration"
  exit 1
fi

FAILURES=0
TOTAL_CONCEPTS=0

while IFS= read -r -d '' file; do
  fname="$(basename "$file")"
  if [ "$fname" = "index.md" ] || [ "$fname" = "log.md" ]; then
    continue
  fi

  TOTAL_CONCEPTS=$((TOTAL_CONCEPTS + 1))
  rel_path="${file#"${KNOWLEDGE_DIR}/"}"

  first_line=$(head -n 1 "$file")
  if [ "$first_line" != "---" ]; then
    echo "❌ [E1] Missing YAML frontmatter opening in ${rel_path}"
    FAILURES=$((FAILURES + 1))
    continue
  fi

  for req_field in "id:" "title:" "type:" "status:" "trust:"; do
    if ! grep -q "^${req_field}" "$file"; then
      echo "❌ [E2] Missing required field '${req_field}' in ${rel_path}"
      FAILURES=$((FAILURES + 1))
    fi
  done

  status_val=$(grep '^status:' "$file" | awk '{print $2}' | tr -d '"')
  case "$status_val" in
    active|stable|draft|deprecated) ;;
    *)
      echo "❌ [E3] Invalid status '${status_val}' in ${rel_path}"
      FAILURES=$((FAILURES + 1))
      ;;
  esac

done < <(find "${KNOWLEDGE_DIR}" -type f -name "*.md" -print0)

if [ $FAILURES -eq 0 ]; then
  echo "✓ OKF v0.2 validation passed! (${TOTAL_CONCEPTS} concepts verified, 0 errors)"
  exit 0
else
  echo "❌ OKF validation failed with ${FAILURES} errors."
  exit 1
fi
