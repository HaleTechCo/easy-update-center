Perfect. I dug into the upstream implementation, and there is one correction to my earlier sketch: **we should use Apache HTTPD for the version routing**, because the official `update-center2` tooling generates `mod_rewrite` rules specifically for this purpose. Jenkins' own documentation describes the architecture the same way: versioned requests are redirected to tiered `dynamic-*` sites.  GitHub+1

 The upstream generator also explicitly supports generating `plugin-versions.json`, `release-history.json`, `plugin-documentation-urls.json`, download links, latest-core metadata, and plugin counts.  GitHub

 ### The project I'd build

```
jenkins-update-center/
├── docker-compose.yml
├── .env.example
├── generator/
│   ├── Dockerfile
│   └── generate.sh
├── httpd/
│   ├── Dockerfile
│   └── httpd.conf
├── config/
│   ├── arguments.txt
│   └── resources/
│       └── ...
├── certs/
│   ├── update-center.crt
│   └── update-center.key
└── data/
    ├── www/
    └── downloads/
```

 The runtime would be:

```
                 Internet
                    │
                    ▼
          ┌───────────────────┐
          │     Apache HTTPD  │
          │                   │
          │ ?version=2.555.3  │
          └─────────┬─────────┘
                    │
             mod_rewrite
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    dynamic-2.555.3/      dynamic-2.54x/
          │                   │
          └─────────┬─────────┘
                    ▼
              static files
                    ▲
                    │
          ┌─────────┴─────────┐
          │ update-center2    │
          │                   │
          │ generator         │
          └───────────────────┘
```

 That's quite close to the Jenkins project's actual architecture. The reason for the multiple `dynamic-*` sites is important: an update center only advertises one version of a plugin, so an old Jenkins mustn't be handed the newest plugin if that version requires a newer Jenkins core.  GitHub

 ### One other important detail: signing

 I would **absolutely keep JSON signing enabled** rather than making this an unsigned private endpoint.

 `update-center2` supports signing with:

```
--key
--certificate
--root-certificate
```

 and Jenkins can trust your certificate by placing its CA certificate under:

```
$JENKINS_HOME/update-center-rootCAs/
```

 The upstream documentation explicitly describes this workflow.  GitHub

 That means we can have:

```
                     ┌─────────────────┐
                     │ update-center   │
                     │ signing key     │
                     └────────┬────────┘
                              │
                              ▼
              signed update-center.json
                              │
                              ▼
                         Jenkins
                              │
                    trusts your root CA
```

 rather than weakening Jenkins' update-site verification.

 ### What the resulting site will look like

 For example:

```
https://jenkins-updates.example.com/update-center.json
https://jenkins-updates.example.com/update-center.actual.json

https://jenkins-updates.example.com/update-center.json?version=2.555.3

https://jenkins-updates.example.com/current/plugin-versions.json
https://jenkins-updates.example.com/current/release-history.json
https://jenkins-updates.example.com/current/plugin-documentation-urls.json

https://jenkins-updates.example.com/download/plugins/git/...
https://jenkins-updates.example.com/download/war/...
```

 And, crucially, the versioned request:

```
/update-center.json?version=2.555.3
```

 will resolve to the generated tier appropriate for that Jenkins version rather than us trying to dynamically generate JSON on every request.

 That's exactly the behavior Jenkins documents for its default update site.  Jenkins

 ### About plugin downloads

 There's another subtlety worth getting right.

 The update-center JSON contains download URLs. If you want this to be a **true self-hosted mirror**, those URLs need to point at your own download host; otherwise your Jenkins will successfully use your update center but then fetch the `.hpi/.jpi` files from `updates.jenkins.io`.

 The Jenkins Plugin Installation Manager documentation explicitly calls this out for mirrors: metadata and binary download routing are separate concerns.  GitHub

 So I'd make the configuration support:

```
UPDATE_CENTER_URL=https://jenkins-updates.example.com
DOWNLOAD_URL=https://jenkins-updates.example.com/download
```

 and have the generated metadata use the latter.

 ### One caveat

 The upstream `update-center2` project says itself that it's primarily optimized for Jenkins infrastructure and that private/corporate deployments may require customization.  GitHub

 That's actually useful for us: **we don't need to fork/reimplement the generator.** We can package the upstream generator and provide the missing deployment glue around it.

 The current upstream release visible in the repository is `3.18.3`; it includes the recent fixes/features in the 3.18 line.  GitHub

---

 If you're going to actually deploy this, I'd make the next step the **complete repository contents** rather than another architectural description: Dockerfiles, Compose file, generator configuration, Apache version-routing configuration, certificate generation, scheduled regeneration, persistent volumes, and the Jenkins-side configuration.
