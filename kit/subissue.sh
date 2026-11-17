#!/usr/bin/env bash
# Link CHILD as a native sub-issue of PARENT. Usage: subissue.sh OWNER/REPO PARENT# CHILD#
set -euo pipefail
R=${1:?repo}; P=${2:?parent}; C=${3:?child}
ID=$(gh api "repos/$R/issues/$C" --jq .id)   # REST wants the child's numeric id, not its number
gh api -X POST "repos/$R/issues/$P/sub_issues" -F sub_issue_id="$ID" --jq '"linked #'"$C"' under #'"$P"'"'
