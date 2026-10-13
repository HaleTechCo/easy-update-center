#!/usr/bin/env bash
# Opens, or updates, one issue listing the High and Critical vulnerabilities with a fix that Grype found in the latest release's
# images, and closes it once a scan finds none. Run weekly by .github/workflows/scheduled.yml from the repository root, with GH_TOKEN
# and GH_REPO; each argument is IMAGE=REPORT, REPORT being Grype's JSON report for IMAGE.
# What to do about the issue: docs/development/releasing.md#security-fixes-in-the-base-images.
# shellcheck disable=SC2154 # GITHUB_SERVER_URL, GITHUB_REPOSITORY and GITHUB_RUN_ID are set by GitHub Actions
set -euo pipefail

title='Fixable vulnerabilities in the published images'
repository_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}"
run_url="${repository_url}/actions/runs/${GITHUB_RUN_ID}"
sections="$(mktemp)"
body="$(mktemp)"
total=0
for argument in "$@"; do
    image="${argument%%=*}"
    report="${argument#*=}"
    rows="$(jq -r '
        [.matches[]
         | select(.vulnerability.fix.state == "fixed")
         | select(.vulnerability.severity == "Critical" or .vulnerability.severity == "High")
         | "| \(.vulnerability.id) | \(.vulnerability.severity) | \(.artifact.name) \(.artifact.version) | \(.vulnerability.fix.versions | join(", ")) |"]
        | unique | .[]' "${report}")"
    count="$(grep -c . <<< "${rows}" || true)"
    total=$((total + count))
    {
        echo
        echo "### ${image}"
        echo
        if ((count == 0)); then
            echo 'None.'
        else
            echo '| Vulnerability | Severity | Package | Fixed in |'
            echo '|---|---|---|---|'
            echo "${rows}"
        fi
    } >> "${sections}"
done

open="$(gh issue list --state open --search "${title} in:title" --json number,title --jq ".[] | select(.title == \"${title}\") | .number" | head -n 1)"
if ((total == 0)); then
    if [[ -n "${open}" ]]; then
        gh issue close "${open}" --comment "The scan in ${run_url} found none: closing."
    else
        echo 'no High or Critical vulnerability with a fix in the published images'
    fi
    exit 0
fi

{
    echo "The weekly scan of the latest release's images found ${total} High or Critical vulnerabilities that have a fix."
    echo "What to do: ${repository_url}/blob/main/docs/development/releasing.md#security-fixes-in-the-base-images"
    cat "${sections}"
    echo
    echo "The scan: ${run_url}"
} > "${body}"
truncate --size='<60000' "${body}" # an issue body holds at most 65536 characters
if [[ -n "${open}" ]]; then
    gh issue edit "${open}" --body-file "${body}"
else
    gh issue create --title "${title}" --body-file "${body}"
fi
