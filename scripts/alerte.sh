#!/usr/bin/env bash
# Ouvre (ou complète) une issue GitHub « publication manquée » : notification e-mail/app GitHub au propriétaire.
set -u
day=$(TZ=Europe/Paris date +%F)
title="AutoPost : publication manquée ou en échec le $day"
body="Exécution : ${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}

$(cat "${ALERT_FILE:-/tmp/autopost-alerte.txt}" 2>/dev/null || echo 'Voir le résumé de l exécution et data/log.jsonl.')

Rattrapage sans doublon : workflow « Rattrapage d'une publication » (un canal à la fois)."
num=$(gh issue list --state open --search "\"$title\" in:title" --json number --jq '.[0].number' 2>/dev/null)
if [ -n "${num:-}" ]; then
  last=$(gh issue view "$num" --json comments --jq '.comments[-1].body // ""' 2>/dev/null | tail -n +3)
  new=$(printf '%s' "$body" | tail -n +3)
  [ "$last" = "$new" ] && { echo "Alerte déjà signalée (inchangée)"; exit 0; }
  gh issue comment "$num" --body "$body"
else
  gh label create autopost-alerte --color D93F0B --force >/dev/null 2>&1 || true
  gh issue create --title "$title" --body "$body" --label autopost-alerte
fi
