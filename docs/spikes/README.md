# Spikes needing an operator (spec §15)

S3 was replaced by the fake Artifactory. S4 is covered by `make it-airgapped`. S5 was resolved
during planning. The rest need real systems.

| # | Question | Procedure | Result (date, who, outcome) |
|---|---|---|---|
| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export ARTIFACTORY_USERNAME=… ARTIFACTORY_PASSWORD=…; make it-online`. The first test fails with `HTTP 403` if not. | Blocked 2026-09-22 (implementation session): no Jenkins community account available. `make it-online` correctly refused with exit 2; `make upstream-diff TO=3.18.4` passed. |
| S1b | Can a **non-admin** service account run it on the internal Artifactory? | With `.env` from `.env.airgapped.example` and the service account in `secrets/`: `docker compose run --rm uc doctor --fetch`. An AQL error names the HTTP status. | |
| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `ARTIFACTORY_REPOSITORY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | |
| S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
| S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=… GITHUB_TOKEN=…` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | |

## If an answer is "no"

- **S1a no:** online mode needs a private Artifactory remote repository in front of repo.jenkins-ci.org.
  Test whether AQL on a remote repository lists more than its cache (S2-style) before recommending it.
- **S1b no:** document the Artifactory permission the service account needs, then repeat S1b.
- **S2 no:** keep the documented contract (a single local repository).
- **S6:** set the `JAVA_TOOL_OPTIONS` guidance and volume sizing in docs/operations.md from the numbers.
- **S7 no:** document the token type that works, or accept that GitHub labels are omitted.
