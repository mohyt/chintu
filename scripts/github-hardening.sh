#!/usr/bin/env bash
# Repository protections to apply right after the repository is made public.
# Secret scanning, push protection and branch protection are only available to a
# public repository on a free account, so this cannot run while it is private.
# Dependabot alerts and automated security fixes work either way and are applied too.
# Requires `gh auth login` with the repo scope. Safe to re-run.
set -euo pipefail

repo="${1:-mohyt/chintu}"
branch="${2:-main}"

visibility="$(gh api "repos/$repo" --jq .visibility)"
echo "==> $repo is $visibility"

echo "==> Dependabot alerts and automated security fixes"
gh api -X PUT "repos/$repo/vulnerability-alerts" > /dev/null
gh api -X PUT "repos/$repo/automated-security-fixes" > /dev/null

if [ "$visibility" != "public" ]; then
  echo "Secret scanning and branch protection need a public repository; make it public, then re-run." >&2
  exit 2
fi

echo "==> Secret scanning with push protection"
gh api -X PATCH "repos/$repo" --input - > /dev/null <<'JSON'
{"security_and_analysis": {"secret_scanning": {"status": "enabled"},
                            "secret_scanning_push_protection": {"status": "enabled"}}}
JSON

echo "==> Branch protection on $branch: CI must pass, no force pushes, no deletion"
# enforce_admins stays off so the owner can still push signed commits straight to main;
# everyone else goes through a pull request that must pass the verify workflow.
gh api -X PUT "repos/$repo/branches/$branch/protection" --input - > /dev/null <<'JSON'
{"required_status_checks": {"strict": true, "contexts": ["python-and-core", "android", "secrets"]},
 "enforce_admins": false,
 "required_pull_request_reviews": null,
 "restrictions": null,
 "allow_force_pushes": false,
 "allow_deletions": false,
 "required_conversation_resolution": true}
JSON

echo "==> Actions: workflows get read-only tokens unless a job asks for more"
gh api -X PUT "repos/$repo/actions/permissions/workflow" --input - > /dev/null <<'JSON'
{"default_workflow_permissions": "read", "can_approve_pull_request_reviews": false}
JSON

gh api "repos/$repo" --jq '.security_and_analysis'
gh api "repos/$repo/branches/$branch/protection" --jq '{checks: .required_status_checks.contexts, force: .allow_force_pushes.enabled, delete: .allow_deletions.enabled}'
echo "done"
