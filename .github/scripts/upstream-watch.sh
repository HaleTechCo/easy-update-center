#!/usr/bin/env bash
# Opens, or updates, one issue (and leaves a closed one alone) when update-center2 has a release newer than the pin in uc/Dockerfile.
# Run weekly by .github/workflows/scheduled.yml from the repository root, with GH_TOKEN and GH_REPO.
# Moving to the new release: docs/development/upgrading-upstream.md.
# shellcheck disable=SC2154 # GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_RUN_ID are set by GitHub Actions
set -euo pipefail

pinned="$(sed -n 's/^ARG UC2_VERSION=//p' uc/Dockerfile)"
tags="$(git ls-remote --tags --refs https://github.com/jenkins-infra/update-center2.git 'update-center2-*')"
newest="$(sed -n 's#^.*refs/tags/update-center2-\([0-9][0-9.]*[0-9]\)$#\1#p' <<< "${tags}" | sort -V | tail -n 1)"
if [[ -z "${pinned}" || -z "${newest}" ]]; then
    echo "::error::cannot compare update-center2 releases: pinned '${pinned}', newest '${newest}'"
    exit 1
fi
highest="$(printf '%s\n%s\n' "${pinned}" "${newest}" | sort -V | tail -n 1)"
if [[ "${highest}" == "${pinned}" ]]; then
    echo "update-center2 ${pinned} is the newest release"
    exit 0
fi

# The comparison builds the uc test image; a failure still opens the issue, then fails the run.
report="$(mktemp)"
body="$(mktemp)"
rc=0
make -s upstream-diff TO="${newest}" > "${report}" 2>&1 || rc=$?
cat "${report}"

stat="$(awk '/^== files uc depends on/ { exit } /^== files changed/ { found = 1 } found' "${report}")"
verdict="$(grep -E '^== (patches apply|PATCHES DO NOT APPLY)' "${report}" || true)"
repository_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}"
{
    echo "update-center2 ${newest} is out; uc/Dockerfile pins ${pinned}."
    echo "Moving to it: ${repository_url}/blob/main/docs/development/upgrading-upstream.md"
    echo
    echo '~~~text'
    echo "${stat}"
    echo "${verdict:-make upstream-diff exited ${rc} before checking the patches}"
    echo '~~~'
    echo
    echo "The full diff of the files uc depends on is in the run's log: ${repository_url}/actions/runs/${GITHUB_RUN_ID}"
} > "${body}"
truncate --size='<60000' "${body}" # an issue body holds at most 65536 characters

title="update-center2 ${newest} is available"
found="$(gh issue list --state all --search "${title} in:title" --json number,title,state --jq ".[] | select(.title == \"${title}\") | \"\(.number) \(.state)\"" | head -n 1)"
number="${found%% *}"
state="${found#* }"
if [[ -z "${found}" ]]; then
    gh issue create --title "${title}" --body-file "${body}"
elif [[ "${state}" == "OPEN" ]]; then
    gh issue edit "${number}" --body-file "${body}"
else
    echo "issue #${number} for ${title%% is available} is ${state,,}: leaving it alone"
fi
exit "${rc}"
