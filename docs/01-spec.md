Exactly - and your clarification makes it much clearer. You want to **self-host the Jenkins update-site infrastructure itself**, not merely put a copy of `update-center.json` behind Nginx.

 I checked the current Jenkins implementation, and fortunately Jenkins actually provides the generator we need: [`jenkins-infra/update-center2`](<https://github.com/jenkins-infra/update-center2>). It is the same project used to generate the public `updates.jenkins.io` site.  GitHub+1

 The current generator explicitly supports the pieces you're asking about, including:

 - version-specific update sites
- `?version=<jenkins-version>` routing
- `update-center.json`
- `update-center.actual.json`
- `plugin-versions.json`
- `release-history.json`
- `plugin-documentation-urls.json`
- `pluginCount.txt`
- Jenkins core version metadata
- plugin dependency/core-version filtering
- download artifacts
- latest/core links
- experimental releases
- security warnings and deprecations

 The official generator's own example invocation includes `--generate-plugin-versions`, `--generate-release-history`, `--generate-plugin-documentation-urls`, `--write-latest-core`, and the download/link generation options.  GitHub

 And importantly, the **versioned proxy behavior is part of the official architecture**: Jenkins sends something like:

```
/update-center.json?version=2.555.3
```

 and the update-site frontend routes that request to an appropriate generated site containing the newest plugin versions compatible with that Jenkins core version.  GitHub

 ## What I'd build for you

 I'd make this as a small Docker-based deployment with roughly this architecture:

```
                         ┌──────────────────────┐
                         │   Jenkins Controller  │
                         │                      │
                         │ ?version=2.555.3     │
                         └──────────┬───────────┘
                                    │
                                    ▼
                     ┌───────────────────────────┐
                     │ update-center.example.com │
                     │                           │
                     │      nginx / Apache       │
                     │          │                │
                     │          ▼                │
                     │   version router          │
                     │          │                │
                     │    ┌─────┴─────┐          │
                     │    ▼           ▼          │
                     │ dynamic-*    current      │
                     │    │           │          │
                     └────┼───────────┼──────────┘
                          │           │
                          ▼           ▼
                    generated      generated
                    metadata      metadata
```

 With a data layout along the lines of:

```
site/
├── update-center.json
├── update-center.actual.json
├── plugin-versions.json
├── release-history.json
├── plugin-documentation-urls.json
├── pluginCount.txt
├── latestCore.txt
├── timestamp.txt
│
├── current/
│   ├── update-center.json
│   ├── update-center.actual.json
│   ├── plugin-versions.json
│   └── ...
│
├── dynamic-2.555.3/
│   ├── update-center.json
│   └── ...
│
├── dynamic-2.516.1/
│   ├── update-center.json
│   └── ...
│
└── downloads/
    ├── plugins/
    └── war/
```

 The actual Jenkins project uses this **tiered/dynamic update-site concept** specifically because one `update-center.json` cannot safely represent every Jenkins version: newer plugin releases can require newer Jenkins cores, while older Jenkins versions may still have compatible older releases.  GitHub

 ### And I wouldn't implement the version routing in application code

 That's one of the confusing parts of the Jenkins infrastructure.

 The generator creates the static sites, and the public Jenkins infrastructure uses web-server routing rules to map:

```
?version=2.555.3
```

 to the appropriate generated `dynamic-*` site. The repository's `site/generate.sh` and `generate-htaccess.sh` are specifically involved in this process.  GitHub

 So for Docker, we can make the runtime extremely boring:

```
generator container
        │
        │ generates
        ▼
shared volume
        │
        ▼
nginx container
        │
        ▼
Jenkins
```

 That has a nice property: **the update center is just static content at runtime**. Nginx doesn't need to understand Jenkins plugin metadata; it only needs to reproduce the version-routing behavior.

 ## One important design decision

 There are actually two possible meanings of "self-host":

 **A. Private mirror of Jenkins**

```
your Jenkins
     ↓
your update center
     ↓
mirror Jenkins plugins/releases
```

 You still consume the official Jenkins plugin ecosystem, but your Jenkins controllers never need to contact `updates.jenkins.io` directly.

 **B. Completely independent update ecosystem**

```
your Jenkins
     ↓
your update center
     ↓
your Artifactory/Nexus/plugin repository
```

 Here your update center becomes the metadata layer for your own plugin repository as well.

 The `update-center2` project is capable of being adapted for corporate/private update sites, although Jenkins explicitly warns that the project is optimized for Jenkins infrastructure and that private-site users may need customization.  GitHub

 **For what you've described, I'd start with A**, while designing the Docker setup so it can later become B.

 That gives you the complete Jenkins behavior without making us reinvent plugin metadata collection.

 And this isn't just theoretical: the current public Jenkins `/current/` site contains exactly the auxiliary files you're talking about, including `plugin-versions.json`, `release-history.json`, `plugin-documentation-urls.json`, `latestCore.txt`, etc.  Jenkins Update Sites

 If you're happy with that architecture, I can lay out the actual project next: **`Dockerfile`, generator image, Nginx config for `?version=`, scheduled regeneration, persistent storage, plugin/war mirroring, and a `docker-compose.yml` you can start with one command.**
