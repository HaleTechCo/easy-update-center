# Spikes needing an operator (spec §15)

S3 was replaced by the fake Artifactory. S4 is covered by `make it-airgapped`. S5 was resolved
during planning. S1a and S7 were answered on 2026-09-22 against the public services. What remains —
S1b, S2 and S6 — needs your own Artifactory or hours of runtime.

| # | Question | Procedure | Result (date, who, outcome) |
|---|---|---|---|
| S1a | Can a Jenkins community account run the generator's AQL query on repo.jenkins-ci.org? | `export ARTIFACTORY_USERNAME=… ARTIFACTORY_PASSWORD=…; make it-online`. The first test fails with `HTTP 403` if not. | **Yes** — 2026-09-22, operator's community account. `make it-online` passed (6 passed, 1 skipped; the skip is S7). Anonymous AQL returns 403, the same account authenticated returns 200. Online mode needs no private Artifactory mirror. |
| S1b | Can a **non-admin** service account run it on the internal Artifactory? | With `.env` from `.env.airgapped.example` and the service account in `secrets/`: `docker compose run --rm uc doctor --fetch`. An AQL error names the HTTP status. | |
| S2 | Does AQL work against a **virtual** repository? | Repeat S1b with `ARTIFACTORY_REPOSITORY=<virtual repo>`; compare `Plugins with a POM` with the local repository's count. | |
| S6 | Full-catalog first run: duration, heap, cache and site size. | `make it-full` (hours); copy `tests/integration/.results/online-*.json` here. If it fails with `OutOfMemoryError`, raise `-Xmx` in the `it-full` target and rerun. | |
| S7 | Does GitHub GraphQL accept username + token over basic auth? | `export GITHUB_USERNAME=… GITHUB_TOKEN=…` (a token with no scopes), then `make it-online`; `test_github_labels_when_a_token_is_given` must pass rather than skip. | **Yes** — 2026-09-22, operator's account with a scopeless classic token. `make it-online` passed 7/7 with no skip. A direct `POST https://api.github.com/graphql` with `--user name:token` returned 200 and real `repositoryTopics`. |

## Notes from answered spikes

- **S1a:** AQL needs authentication (anonymous is 403) but no special grant — it returns whatever the
  account can read, and everyone can read `releases`. Artifactory rejects any AQL query that does not
  `include` all of `repo`, `path` and `name`, with `HTTP 400 "For permissions reasons AQL items domain
  demands the following fields"`. `AQL_TEMPLATE` in `uc/uc/aql.py` already includes all three; keep it
  that way when editing the query.
- **S7:** a **classic token with no scopes** is enough; GitHub's GraphQL endpoint accepts it as the
  password in basic auth, which is what upstream's `GitHubSource` sends. Labels cost about 70 s on a
  25-plugin run and grow the site slightly (24,685,552 → 24,708,724 bytes) — that growth is the
  labels landing in the JSON.
- Capped reference runs (25 plugins, `-Xmx2g`): 247 s without GitHub, 316 s with; 466 KB cache,
  ~23.5 MB site, 5 stable and 5 weekly tiers. Not a substitute for S6, which measures the full
  catalogue.

## If an answer is "no"

- **S1b no:** document the Artifactory permission the service account needs, then repeat S1b.
- **S2 no:** keep the documented contract (a single local repository).
- **S6:** set the `JAVA_TOOL_OPTIONS` guidance and volume sizing in docs/operations.md from the numbers.
