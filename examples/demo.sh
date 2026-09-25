#!/usr/bin/env sh
# End-to-end offline demo: copy the sample repo, init git, run the fleet with the
# scripted backend (no API key needed), then show the resulting branch.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
export FLEET_HOME="${FLEET_HOME:-$work/fleet-home}"
cp -R "$here/sample-repo" "$work/shop"
cd "$work/shop"
git init -q -b main
git add -A
git -c user.name=demo -c user.email=demo@example.com commit -q -m "sample shop with two bugs"

echo "== before: tests"
python3 -m unittest 2>&1 | tail -1 || true

fleet run "$work/shop" "Fix the failing tests" --backend scripted \
  --script "$here/scripts/sample-fix.json" --test-cmd "python -m unittest -v" "$@"

tid=$(fleet status --limit 1 | awk 'NR==2 {print $1}')
echo "== fleet/$tid"
git log --oneline --graph "main..fleet/$tid"
git diff "main" "fleet/$tid"
echo "== trajectories: $FLEET_HOME/runs/$tid"
