#!/usr/bin/env bash
# Checks for uc/bin/uc-setup, the setup service's entrypoint. Runs as root inside the uc image, lays
# /setup out as compose.yaml mounts it, and runs uc-setup itself as the host user (1000), as the setup
# service does with UC_UID=1000. Usage, from the Makefile: make test-setup
set -uo pipefail
setup=/scripts/uc-setup
secrets=/setup/secrets
host=1000:1000
usage_line='usage: uc-setup [secrets | pki-init [--out DIR] [--org ORG] [--root-days N] [--signing-days N] | pki-rotate [--days N] [--org ORG]]'
credentials='repo_username repo_password github_username github_token download_proxy_auth repo_push_auth'
failures=0
output=''
rc=0

pass() { echo "ok: $1"; }

fail() {
    echo "FAIL: $1"
    failures=$((failures + 1))
}

same() { # same DESCRIPTION EXPECTED ACTUAL
    if [[ "$3" == "$2" ]]; then pass "$1"; else fail "$1 (expected '$2', got '$3')"; fi
}

stat_is() { # stat_is DESCRIPTION FORMAT EXPECTED PATH
    local actual
    actual="$(stat -c "$2" "$4" 2>&1)"
    same "$1" "$3" "${actual}"
}

says() { # says DESCRIPTION TEXT - the last run's output holds TEXT
    if [[ "${output}" == *"$2"* ]]; then pass "$1"; else fail "$1 (no '$2' in: ${output})"; fi
}

is_file() { # is_file DESCRIPTION PATH
    if [[ -f "$2" ]]; then pass "$1"; else fail "$1 ($2 is missing)"; fi
}

run() { # run [ARG...] - uc-setup as the host user; sets rc and output
    output="$(setpriv --reuid="${host%%:*}" --regid="${host##*:}" --clear-groups env HOME=/tmp sh "${setup}" "$@" 2>&1)"
    rc=$?
}

fresh() { # fresh - an empty secrets/ owned by the host user; no /setup/out, no /setup/root
    rm -rf /setup
    mkdir -p "${secrets}"
    chown "${host}" /setup "${secrets}"
}

modes() { # modes LABEL DIR - the modes the secrets step sets
    local file
    for file in update-center.key ${credentials}; do
        stat_is "$1: ${file} is 0600" %a 600 "$2/${file}"
    done
    for file in update-center.cert root-ca.crt; do
        stat_is "$1: ${file} is 0644" %a 644 "$2/${file}"
    done
    stat_is "$1: root/ is 0700" %a 700 "$2/root"
    stat_is "$1: root/root-ca.key is 0600" %a 600 "$2/root/root-ca.key"
}

expect_usage() { # expect_usage [ARG...]
    run "$@"
    same "usage: uc-setup $*: exits 2" 2 "${rc}"
    says "usage: uc-setup $*: prints the usage line" "${usage_line}"
}

# 1. First-time setup: every credential file, empty and yours; a new PKI; the modes.
fresh
run
same "first run: exits 0" 0 "${rc}"
for file in ${credentials}; do
    stat_is "first run: ${file} exists, empty, yours" %s:%u 0:1000 "${secrets}/${file}"
done
is_file "first run: update-center.key exists" "${secrets}/update-center.key"
says "first run: says to move the root key offline" "move secrets/root/root-ca.key to offline storage"
modes "first run" "${secrets}"

# 2. Again: the keys stay; a credential written meanwhile keeps its contents and gets its mode back.
before="$(sha256sum < "${secrets}/update-center.key")"
printf '%s' svc-uc > "${secrets}/repo_username" && chmod 0644 "${secrets}/repo_username"
run
same "second run: exits 0" 0 "${rc}"
says "second run: leaves the keys" "the signing keys exist; left as they are"
after="$(sha256sum < "${secrets}/update-center.key")"
same "second run: update-center.key unchanged" "${before}" "${after}"
contents="$(cat "${secrets}/repo_username")"
same "second run: repo_username keeps its contents" svc-uc "${contents}"
stat_is "second run: repo_username is 0600 again" %a 600 "${secrets}/repo_username"

# 3. A secrets/ the setup user cannot write (UC_UID unset or wrong): stop, naming UC_UID; nothing created.
fresh
chown 0:0 "${secrets}" && chmod 0755 "${secrets}"
run secrets
same "not writable: exits 1" 1 "${rc}"
says "not writable: names UC_UID and UC_GID" "secrets/ is not writable by uid 1000: set UC_UID and UC_GID in .env to the output of id -u and id -g"
left="$(ls -A "${secrets}")"
same "not writable: nothing created" "" "${left}"

# 3b. A file in secrets/ that belongs to another user: stop, naming the file and UC_UID, not chmod's own error.
fresh
: > "${secrets}/repo_password" && chmod 0644 "${secrets}/repo_password"
run secrets
same "another user's file: exits 1" 1 "${rc}"
says "another user's file: names it and UC_UID" "secrets/repo_password belongs to another user than uid 1000: give it to yours (sudo chown), or set UC_UID and UC_GID in .env to its owner"

# 4. pki-init with options, in another folder: the PKI there with its modes, and no credential files.
fresh
mkdir /setup/out && chown "${host}" /setup/out
run pki-init --out /setup/out --org "Example Corp" --root-days 30 --signing-days 10
same "pki-init --out: exits 0" 0 "${rc}"
is_file "pki-init --out: update-center.cert exists" /setup/out/update-center.cert
stat_is "pki-init --out: update-center.key is 0600" %a 600 /setup/out/update-center.key
extra="$(find /setup/out -maxdepth 1 -type f ! -name 'update-center*' ! -name 'root*')"
same "pki-init --out: no credential files" "" "${extra}"
subject="$(openssl x509 -in /setup/out/update-center.cert -noout -subject)"
if [[ "${subject}" == *"Example Corp"* ]]; then pass "pki-init --out: --org reaches the subject"; else fail "pki-init --out: --org reaches the subject (${subject})"; fi

# 5. pki-rotate: the root key mounted at /setup/root; missing, or a file there, stops it.
fresh
run
mv "${secrets}/root" /setup/root
run pki-rotate --days 20
same "pki-rotate: exits 0 with the root at /setup/root" 0 "${rc}"
says "pki-rotate: names secrets/update-center.cert" "issued a new secrets/update-center.cert"
is_file "pki-rotate: keeps the previous key as update-center.key.old" "${secrets}/update-center.key.old"
if [[ -e "${secrets}/root" ]]; then fail "pki-rotate: leaves nothing at secrets/root"; else pass "pki-rotate: leaves nothing at secrets/root"; fi
stat_is "pki-rotate: update-center.key.old is 0600" %a 600 "${secrets}/update-center.key.old"
rm -rf /setup/root
run pki-rotate
same "pki-rotate without /setup/root: exits 1" 1 "${rc}"
says "pki-rotate without /setup/root: says how to mount it" "docker compose run --rm -v <folder holding root-ca.key>:/setup/root:ro setup pki-rotate"
touch /setup/root
run pki-rotate
same "pki-rotate with a file at /setup/root: exits 1" 1 "${rc}"
says "pki-rotate with a file at /setup/root: says to mount the folder" "/setup/root is a file: mount the folder that holds root-ca.key"

# 7. The uc image never imports from the working directory: uc runs from /data, where other
# containers write, and python3 -m would put it first on sys.path.
mkdir -p /tmp/planted/uc && printf 'raise SystemExit("planted")\n' > /tmp/planted/uc/__init__.py
output="$(setpriv --reuid="${host%%:*}" --regid="${host##*:}" --clear-groups env HOME=/tmp sh -c 'cd /tmp/planted && uc --version' 2>&1)"
says "PYTHONSAFEPATH: uc ignores a uc package in the working directory" "uc "
if [[ "${output}" == *planted* ]]; then fail "PYTHONSAFEPATH: the planted package ran"; fi
rm -rf /tmp/planted

# 8. A killed pki init can leave its work folder, with a root key in it: the next run removes it.
fresh
mkdir -p "${secrets}/.init-stale/root" && : > "${secrets}/.init-stale/root/root-ca.key"
chown -R "${host}" "${secrets}/.init-stale"
run
same "stale init folder: exits 0" 0 "${rc}"
says "stale init folder: says it removed it" "removed secrets/.init-stale, left by an interrupted pki init"
if [[ -e "${secrets}/.init-stale" ]]; then fail "stale init folder: is gone"; else pass "stale init folder: is gone"; fi

# 9. setup refuses to run as root: the files it writes would be root's.
fresh
output="$(sh "${setup}" secrets 2>&1)"
rc=$?
same "as root: exits 1" 1 "${rc}"
says "as root: names UC_UID and UC_GID" "setup must not run as root: set UC_UID and UC_GID in .env"

# 6. Usage.
fresh
expect_usage bogus
expect_usage '' extra
expect_usage secrets extra
expect_usage procurement
expect_usage pki-init --out
expect_usage pki-init --bogus value
expect_usage pki-rotate --root-days 3

if [[ ${failures} -ne 0 ]]; then
    echo "${failures} check(s) failed"
    exit 1
fi
