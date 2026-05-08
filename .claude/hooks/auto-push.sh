#!/usr/bin/env bash
# Auto-push current branch to origin after each Claude turn.
# Retries on transient failures (the local git proxy occasionally returns 503).
# Silent on success — exits 0. On final failure prints a systemMessage so it surfaces in the UI.

set -u

cd /home/user/lucky_hq 2>/dev/null || exit 0
branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
[ -z "$branch" ] && exit 0

# Skip if upstream exists and there are no unpushed commits.
# If there's no upstream yet, we still push (-u creates it).
if git rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1; then
  unpushed=$(git rev-list "@{u}..HEAD" --count 2>/dev/null || echo 0)
  [ "$unpushed" = "0" ] && exit 0
fi

delay=2
last_err=""
for attempt in 1 2 3 4 5; do
  if out=$(git push -u origin "$branch" 2>&1); then
    exit 0
  fi
  last_err="$out"
  if [ "$attempt" -lt 5 ]; then
    sleep "$delay"
    delay=$((delay * 2))
  fi
done

# Surface failure in the UI without blocking Claude.
tail_msg=$(printf '%s' "$last_err" | tail -n 1 | cut -c1-200 | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '{"systemMessage":"auto-push failed on %s after 5 attempts: %s"}\n' "$branch" "$tail_msg"
exit 0
