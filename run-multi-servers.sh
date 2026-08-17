#!/usr/bin/env bash
set -Eeuo pipefail
NODES="${1:-}"; DURATION="${2:-20}"; TOTAL_RATE="${3:-10000}"
case "$NODES" in 10|20|50) ;; *) echo "Usage: $0 <10|20|50> [seconds] [total-tps]" >&2; exit 2;; esac
[[ "$DURATION" =~ ^[1-9][0-9]*$ && "$TOTAL_RATE" =~ ^[1-9][0-9]*$ ]] || exit 2
REMOTE_USER="${REMOTE_USER:-ubuntu}"; REMOTE_DIR="${REMOTE_DIR:-/home/ubuntu/Tusk-Ubuntu24}"
HOSTS_FILE="${HOSTS_FILE:-deploy/hosts-${NODES}.txt}"; SSH_KEY="${SSH_KEY:-$HOME/.ssh/tusk-aws.pem}"
TX_SIZE="${TX_SIZE:-512}"; READY_TIMEOUT="${READY_TIMEOUT:-240}"
SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
[[ -f "$HOSTS_FILE" && -f "$SSH_KEY" ]] || { echo "Missing hosts file or SSH key" >&2; exit 1; }
mapfile -t IPS < <(sed -e 's/#.*//' -e 's/[[:space:]]//g' "$HOSTS_FILE" | awk 'NF')
[[ "${#IPS[@]}" -eq "$NODES" && "$(printf '%s\n' "${IPS[@]}" | sort -u | wc -l)" -eq "$NODES" ]] || { echo "Invalid hosts file" >&2; exit 1; }
RATE_SHARE=$(((TOTAL_RATE+NODES-1)/NODES)); TX_NODES=""
for ip in "${IPS[@]}"; do TX_NODES+="${ip}:3003 "; done
remote(){ ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@$1" "$2"; }
stop_all(){ for ip in "${IPS[@]}"; do remote "$ip" "tmux kill-session -t tusk-client 2>/dev/null || true; tmux kill-session -t tusk-primary 2>/dev/null || true; tmux kill-session -t tusk-worker 2>/dev/null || true" & done; wait || true; }
trap stop_all EXIT INT TERM
for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "test -x '$REMOTE_DIR/target/release/node' && test -x '$REMOTE_DIR/target/release/benchmark_client' && test -f '$REMOTE_DIR/deploy/node-${i}.json'"; done
for ip in "${IPS[@]}"; do remote "$ip" "tmux kill-session -t tusk-client 2>/dev/null || true; tmux kill-session -t tusk-primary 2>/dev/null || true; tmux kill-session -t tusk-worker 2>/dev/null || true; cd '$REMOTE_DIR'; rm -rf run/db-primary run/db-worker run/logs; mkdir -p run/logs" & done; wait
for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "cd '$REMOTE_DIR' && tmux new-session -d -s tusk-worker \"RUST_LOG=info ./target/release/node -vv run --keys deploy/node-${i}.json --committee deploy/committee.json --parameters deploy/parameters.json --store run/db-worker worker --id 0 |& tee run/logs/worker-${i}-0.log\""; done
for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "cd '$REMOTE_DIR' && tmux new-session -d -s tusk-primary \"RUST_LOG=info ./target/release/node -vv run --keys deploy/node-${i}.json --committee deploy/committee.json --parameters deploy/parameters.json --store run/db-primary primary |& tee run/logs/primary-${i}.log\""; done
for ((elapsed=0;elapsed<READY_TIMEOUT;elapsed+=3)); do ready=0; for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "ss -ltn | grep -q ':3003 '" && ready=$((ready+1)) || true; done; ((ready==NODES)) && break; sleep 3; done
((ready==NODES)) || { echo "Workers ready=$ready/$NODES" >&2; exit 1; }
for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "cd '$REMOTE_DIR' && tmux new-session -d -s tusk-client \"RUST_LOG=info ./target/release/benchmark_client '${IPS[$i]}:3003' --size '$TX_SIZE' --rate '$RATE_SHARE' --nodes $TX_NODES |& tee run/logs/client-${i}-0.log\""; done
for ((elapsed=0;elapsed<READY_TIMEOUT;elapsed+=3)); do ready=0; for i in "${!IPS[@]}"; do remote "${IPS[$i]}" "grep -q 'Start sending transactions' '$REMOTE_DIR/run/logs/client-${i}-0.log'" && ready=$((ready+1)) || true; done; echo "clients ready=$ready/$NODES"; ((ready==NODES)) && break; sleep 3; done
((ready==NODES)) || exit 1
sleep "$DURATION"; stop_all; trap - EXIT INT TERM
rm -rf benchmark/logs; mkdir -p benchmark/logs
for i in "${!IPS[@]}"; do for kind in "primary-${i}" "worker-${i}-0" "client-${i}-0"; do scp "${SSH_OPTS[@]}" "${REMOTE_USER}@${IPS[$i]}:$REMOTE_DIR/run/logs/${kind}.log" benchmark/logs/; done; done
cd benchmark
python3 - <<'PY'
from benchmark.logs import LogParser
print(LogParser.process('logs', faults=0).result())
PY
