#!/bin/bash
# Restart stopped card lanes while accepted cards are waiting, until the
# round's deadline. Lane env files pin FACTORY_UNTIL, so a restart keeps the
# original end time.
cd /srv/factory/harness
DEADLINE=$(date -u -d 2026-11-10T08:10:00Z +%s)
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  waiting=$(python3 -c "import cards;print(cards.counts().get('accepted',0))" 2>/dev/null || echo 0)
  if [ "$waiting" -gt 0 ]; then
    for i in 1 2 3 4 5 6 7; do
      if ! systemctl --user is-active --quiet grok-factory-lane@cards-$i && [ ! -f state/cards-$i/stop ]; then
        systemctl --user start grok-factory-lane@cards-$i && echo "$(date -u +%FT%TZ) restarted cards-$i ($waiting accepted waiting)"
      fi
    done
  fi
  sleep 120
done
