# GitHub repository settings

The settings of Easy Update Center for Jenkins's GitHub repository that the workflows rely on, in the
order to apply them. Everything here is free for a public repository in an organization on GitHub Free: Actions
(including the `ubuntu-24.04-arm` runners, free for public repositories only), public packages on
GHCR, build provenance and SBOM attestations, rulesets, Dependabot, and private vulnerability
reporting. Nothing in the workflows needs a paid plan, larger runners or an organization-wide feature.

## 1. Organization settings (HaleTechCo)

- Require two-factor authentication.
- Members' base permission: Read.
- Actions > General: workflow permissions "Read repository contents and packages permissions", and
  "Allow GitHub Actions to create and approve pull requests" off.
- Packages: package creation allows public packages.

## 2. The repository

`easy-update-center` in HaleTechCo, **public**. It must stay public: the `ubuntu-24.04-arm` runners
the release builds on are free only for public repositories.

## 3. Repository settings

- The same Actions settings as in step 1, if the organization's do not already apply.
- Features: Issues on, Wikis off.
- Leave "Immutable releases" off. A re-run with publish-only replaces the Release's assets
  (`gh release upload --clobber`), which immutable releases forbid.

## 4. The release App

The Release workflow's `prepare` job pushes the release commit and its tag to `main` as a GitHub App
of your own, the rulesets' only bypass actor. GitHub does not accept its built-in GitHub Actions app
as a bypass actor, and an App owned by the organization is free.

1. HaleTechCo > Settings > Developer settings > GitHub Apps > New GitHub App:
   - GitHub App name: for example `easy-update-center-release`. Release commits show it as
     `<name>[bot]`.
   - Homepage URL: `https://github.com/HaleTechCo/easy-update-center`.
   - Webhook: untick **Active**.
   - Repository permissions: Contents **Read and write** (Metadata read-only comes with it), nothing
     else.
   - Where can this GitHub App be installed: **Only on this account**.
2. On the App's page, note its **Client ID**, then **Generate a private key** (a `.pem` file
   downloads).
3. **Install App** > HaleTechCo > **Only select repositories**: `easy-update-center`.
4. The repository's Settings > Environments > New environment, named `release`:
   - Deployment branches and tags: **Selected branches and tags**, add `main`.
   - Environment secret `RELEASE_APP_PRIVATE_KEY`: the whole contents of the `.pem` file. Then delete
     the downloaded file; if you ever need the key again, generate a new one and replace the secret.
   - Environment variable `RELEASE_APP_CLIENT_ID`: the Client ID.
   - Optional: add yourself as a required reviewer, so each release waits for your approval before
     it pushes.

Why this holds: only a job that names the `release` environment gets the key, and the environment
serves only workflows running on `main`, so a workflow on another branch cannot get the App's token.
The token the job gets can write this repository's contents only, and expires within an hour. Every
other workflow token leaves `main` alone: the rulesets below bypass only the App, and only
`release.yml`'s `release` job holds `contents: write`, to create the GitHub Release
(`tests/unit/test_workflows.py` pins both). Changing a workflow on `main` still takes a pull request,
so keep write access to people you would let merge to `main` (today one maintainer, with members' base
permission Read).

## 5. Branch ruleset

Settings > Rules > Rulesets > New branch ruleset.

- Name `main`, enforcement Active, target the default branch.
- Bypass list: the release App from step 4 (Add bypass > Apps; it appears once it is installed on the
  repository), bypass mode **Always**, and nothing else. Always covers both the required `ci` check and
  the pull-request rule below, which the release commit skips.
- Rules: restrict deletions; require a pull request before merging, with 0 required approvals
  (GitHub does not let the one maintainer approve their own pull request); require status checks to
  pass, with `ci` (source GitHub Actions); block force pushes.

## 6. Tag ruleset

Name `release tags`, target `v*`, rules: restrict creations, restrict updates, restrict deletions.
The same bypass list as step 5. Only the Release workflow's `prepare` job, as the release App, creates
release tags; the `release` job creates the GitHub Release on the tag that already exists.

## 7. Security and moderation

- Settings > Advanced Security: enable private vulnerability reporting
  ([SECURITY.md](../../SECURITY.md) points reporters to it) and Dependabot alerts. Secret scanning and
  push protection are already on for public repositories.
- Settings > Moderation options > Reported content: accept reports from **All users**, so anyone can
  report a comment, issue or pull request to the admins privately, as the Enforcement section of
  [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md) says (free for public repositories owned by an
  organization). Reports appear under the repository's reported content list for admins to review.

Dependabot's version updates need no setting: `.github/dependabot.yml` opens one weekly pull request
each for the actions, the base images and the lint tools.

## 8. Secrets

Settings > Secrets and variables > Actions: `JENKINS_COMMUNITY_USERNAME` and
`JENKINS_COMMUNITY_PASSWORD`, a free Jenkins community account, for the weekly `it-online` run.
Without them the run is skipped with a notice.

## 9. After the first release

New GHCR packages start private, and anonymous pulls fail until they are public. For
`easy-update-center-uc` and `easy-update-center-web` (the organization's Packages): Package settings >
Change visibility > Public. Check that each is linked to the repository (the images'
`org.opencontainers.image.source` label does that; otherwise Connect repository).
