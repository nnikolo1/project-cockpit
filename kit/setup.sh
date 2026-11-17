#!/usr/bin/env bash
# Idempotent: create/update the kit labels. Usage: setup.sh OWNER/REPO
set -euo pipefail
R=${1:?usage: setup.sh OWNER/REPO}
mk() { gh label create "$1" --force -R "$R" --color "$2" --description "$3" >/dev/null && echo "label $1"; }
# protected: only the Manager may set/remove (enforced by policy.yml)
mk released  0e8a16 "PROTECTED: Manager released this task"
mk P1        b60205 "PROTECTED: priority 1 (Manager only)"
mk P2        d93f0b "PROTECTED: priority 2 (Manager only)"
mk P3        fbca04 "PROTECTED: priority 3 (Manager only)"
mk cancelled 6e7781 "PROTECTED: cancelled by the Manager"
# unprotected: anyone may set
mk must          1d76db "class: must"
mk nice          c5def5 "class: nice to have"
mk module        5319e7 "an uber-task: sub-issues are its tasks"
for t in feature bug decision research doc devops test; do mk "type:$t" ededed "type: $t"; done
