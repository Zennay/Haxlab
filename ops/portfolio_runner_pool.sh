#!/usr/bin/env bash
# Managed on-demand self-hosted runners for less-active Zennay repositories.
# Never run more than one pool listener at a time on the memory-constrained VPS.
set -Eeuo pipefail
ROOT=/home/ubuntu
TEMPLATE=/home/ubuntu/actions-runner-lightup-proof
REPOS="zGuard Woonpoints Mixlab ikea-enhanced-app fuckball Tuddy"
exec 9>/tmp/zennay-portfolio-runner-pool.lock
flock -n 9 || exit 0
test "$(hostname -s)" = vps-bb300bba
test "$(id -un)" = ubuntu
gh auth status >/dev/null 2>&1

unit_for() {
  printf 'actions.runner.Zennay-%s.vps-bb300bba-pool-%s.service' "$1" "$1"
}
root_for() {
  printf '%s/actions-runner-pool-%s' "$ROOT" "$1"
}

if test "$1" = bootstrap; then
  sudo -n true
  test -x "$TEMPLATE/config.sh"
  for repo in $REPOS; do
    runner_root="$(root_for "$repo")"
    unit="$(unit_for "$repo")"
    if test -e "$runner_root/.runner"; then
      echo "$repo: local runner already registered"
      continue
    fi
    install -d -m 0700 "$runner_root"
    # Copy ONLY static runner distribution; no another repo's credentials or jobs.
    tar -C "$TEMPLATE" --exclude='./_work' --exclude='./_diag' \
      --exclude='./.runner' --exclude='./.runner_migrated' \
      --exclude='./.credentials*' --exclude='./.service' \
      --exclude='./.env' --exclude='./.path' -cf - . | tar -C "$runner_root" -xf -
    cd "$runner_root"
    token="$(gh api -X POST "repos/Zennay/$repo/actions/runners/registration-token" --jq .token)"
    ./config.sh --unattended --url "https://github.com/Zennay/$repo" \
      --token "$token" --name "vps-bb300bba-pool-$repo" --labels portfolio-pool,vps \
      --work _work --replace
    unset token
    sudo -n ./svc.sh install ubuntu
    sudo -n systemctl disable "$unit"
    sudo -n systemctl set-property "$unit" MemoryHigh=900M MemoryMax=1800M
    echo "$repo: registered, installed and parked for on-demand scheduling"
  done
  echo 'All pool services installed; bootstrap finished'
  exit 0
fi

if test "$1" != tick; then
  echo 'Usage: portfolio_runner_pool.sh {bootstrap|tick}' >&2
  exit 2
fi

# A service must never be stopped while GitHub says busy, or while a worker exists.
active=''
for repo in $REPOS; do
  unit="$(unit_for "$repo")"
  if systemctl is-active --quiet "$unit"; then
    runner_root="$(root_for "$repo")"
    if pgrep -u ubuntu -f "^$runner_root/bin/Runner.Worker" >/dev/null; then
      echo "$repo: active worker; preserving"
      exit 0
    fi
    busy="$(gh api "repos/Zennay/$repo/actions/runners" \
      --jq '[.runners[] | select(.name == "vps-bb300bba-pool-'"$repo"'") | .busy] | any')"
    if test "$busy" = true; then
      echo "$repo: busy on GitHub; preserving"
      exit 0
    fi
    active="$repo"
  fi
done

# Rotate to the project with the largest waiting queue. No hosted fallback.
chosen=''
max=0
for repo in $REPOS; do
  queued="$(gh api "repos/Zennay/$repo/actions/runs?status=queued&per_page=1" --jq .total_count)"
  if test "$queued" -gt "$max"; then
    chosen="$repo"
    max="$queued"
  fi
done

# Avoid unnecessary start/stop churn while existing jobs consume the VPS.
if test -n "$active" && test "$active" = "$chosen"; then
  echo "$active: runner already available for $max queued workflows"
  exit 0
fi
if test -n "$active"; then
  sudo -n systemctl stop "$(unit_for "$active")"
  echo "$active: idle service parked"
fi
if test -z "$chosen"; then
  echo 'No pool workflows queued; all extra runners parked'
  exit 0
fi
available="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
if test "$available" -lt 2200000; then
  echo "$chosen: waiting for RAM headroom; available_kib=$available"
  exit 0
fi
busy_total=0
for repo in zCloud Haxlab Supa RaiseAI Lightup Ftmo zSSH Discordbot; do
  count="$(gh api "repos/Zennay/$repo/actions/runners" --jq '[.runners[] | select(.busy == true)] | length')"
  busy_total=$((busy_total + count))
done
if test "$busy_total" -ge 3; then
  echo "$chosen: waiting for busy workers ($busy_total active)"
  exit 0
fi
sudo -n systemctl start "$(unit_for "$chosen")"
echo "$chosen: on-demand self-hosted listener started for $max queued workflows"
