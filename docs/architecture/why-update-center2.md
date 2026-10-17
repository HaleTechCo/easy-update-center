# Why wrap update-center2

Why this project runs the upstream generator and builds around it, instead of reimplementing it in
a language of its own. This page is for contributors weighing changes to the generator side.

## The choice

[update-center2](https://github.com/jenkins-infra/update-center2) is the Java program the Jenkins
project runs to build updates.jenkins.io. This repository builds a pinned release of it, applies
two small patches (`uc/patches/`), and adds everything around it: containers, scheduling,
validation, atomic publishing, signing keys, air-gapped mode, procurement and the
[repo-adapter](repo-adapter.md).

Rewriting the generator, for example in Python like the rest of `uc`, would remove the Java build,
the patches and the repo-adapter. It would also give up the reasons below.

## What wrapping buys

- **The same output as updates.jenkins.io.** Controllers get exactly the files they already expect,
  produced by the code that produces them for the whole Jenkins community. That is the project's
  main promise, and it holds only while the generator is upstream's.
- **A decade of domain rules, maintained by others.** These are some of the rules update-center2
  encodes:
  - tier computation and version routing;
  - dependency and core-version rules;
  - deprecations, security warnings and labels;
  - `plugin-versions.json`, `release-history.json` and the download index pages.

  An upgrade picks up upstream's fixes to all of these with a version bump
  ([Upgrading upstream](../development/upgrading-upstream.md)).
- **Signatures controllers accept.** Jenkins checks the signature over a specific canonical form of
  the JSON. A reimplementation that differs in any detail produces metadata every controller
  rejects. Using upstream's signer removes that risk.

## What wrapping costs

At 0.1.0, roughly a fifth of the `uc` package exists only because the generator is not ours:

- the [repo-adapter](repo-adapter.md), which answers update-center2's Artifactory requests from
  Artifactory or Artifact Keeper and fills in POMs, descriptions and release dates from the plugin
  archives;
- the glue that runs the generator and prepares its input (`uc/uc/jar.py`, `aql.py`, `layout.py`);
- two patches with 21 changed lines, kept current on every upgrade;
- a Java build in the uc image, and the weekly job that watches upstream for new releases.

Everything else would be needed with any generator: procurement, `uc doctor`, validation,
publishing and rollback, the PKI, the scheduler, the web container and the release process.

## When to reconsider

A reimplementation becomes worth its cost if one of these happens:

- upstream stops being maintained, or changes in ways that work against this project;
- the repo-adapter and the patches grow faster than the generator logic they work around;
- a feature is needed that upstream's design cannot take.

## The path short of a rewrite

update-center2 reads repositories through an interface, `MavenRepository`, whose Artifactory
implementation, `ArtifactoryRepositoryImpl`, builds on an abstract `BaseMavenRepository`. Two
contributions upstream would shrink the wrapper:

- **A second implementation of that interface**, for Artifact Keeper or for a plain Maven layout.
  It would retire most of the repo-adapter.
- **The two patches**: popularity data made optional, and the core age for tiers made a setting.

Either way the generator stays upstream's, and this repository keeps what only it provides:
deployment, air-gapped operation, procurement and release engineering.
