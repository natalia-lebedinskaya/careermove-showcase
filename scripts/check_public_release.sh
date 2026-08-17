#!/usr/bin/env bash
set -euo pipefail

forbidden_files='(^|/)(\.env(\..+)?|.*\.(sqlite3|db|docx|xlsx|xls)|CareerMove-Job-Tracker.*|careermove-google-sheets\.gs)$'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  public_files="$(git ls-files | grep -vE '(^|/)\.env\.example$')"
else
  public_files="$(find . \
    \( -path './.git' -o -path './.venv' -o -path './data' -o -path './.playwright-cli' -o -path './web/node_modules' -o -path './web/dist' \) -prune \
    -o -type f -not -name '.env.example' -print | sed 's#^\./##')"
fi
if printf '%s\n' "$public_files" | grep -Eiq "$forbidden_files"; then
  echo "Public-release check failed: a private data or secret file is present."
  printf '%s\n' "$public_files" | grep -Ei "$forbidden_files"
  exit 1
fi

forbidden_text='(/Users/[^/]+/(Documents|Desktop)|naraliabedareva|Natalia Lebedinskaya|Alexander Lebedinsky|Наталия Лебединская|Александр Лебединский|natalia\.lebedinskaya@|aleksandr\.lebedinskii@|lebedinskayanataliaqa@|ProhaskoNatalia|LebedinskyAA|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,})'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  matches="$(git grep -IinE "$forbidden_text" -- . ':!scripts/check_public_release.sh' ':!web/package-lock.json' || true)"
else
  matches="$(rg -Iin --hidden \
    --glob '!.git/**' --glob '!.venv/**' --glob '!data/**' --glob '!.playwright-cli/**' \
    --glob '!web/node_modules/**' --glob '!web/dist/**' --glob '!package-lock.json' \
    --glob '!check_public_release.sh' "$forbidden_text" . || true)"
fi
if [[ -n "$matches" ]]; then
  printf '%s\n' "$matches"
  echo "Public-release check failed: personal data, a local path, or a secret-like value is present."
  exit 1
fi

echo "Public-release check passed."
