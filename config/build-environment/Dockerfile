# THE BUILD ENVIRONMENT (DN-142.D30): the one image every keyed act runs in. Its CONFIG DIGEST is the
# `system` member of every key, pinned in config/build-environment.json beside the manifest digest a
# daemon runs it by and the archive it loads from; this file is its reviewable provenance and keys
# nothing (two builds of it differ in bytes). It is never pushed to a registry: Argos builds it on the
# desk with `docker buildx build --platform linux/amd64 --provenance=false --sbom=false --load`, writes
# it once with `docker save` to /home/windows/build-environment/build-environment-<config digest>.tar,
# and commits the pin in the same malf commit.
#
# WHAT IS IN IT: the packages a store-create act runs, each apt one pinned to the version the desk ran
# on 2026-10-18 from snapshot.ubuntu.com at that day's index; the ship compiler, the sha256-pinned
# gcc-16.2 build malf's build-gcc-toolchain.yml publishes, at /opt/gcc-16.2 where the profile names
# it and behind the system `gcc`/`cc` names as on the desk; and a venv of hashed wheels (conan, cmake,
# ninja, PyYAML, the jsonschema closure). The clang-21 legs come from apt.llvm.org at build time, which
# no snapshot serves, so they are UNPINNED (DN-142.D30 (7) R3: the key is the image's digest, never this
# file) and the source is trusted only under the LLVM archive key, checked by fingerprint; the versions
# installed are read back into the pin's commit. Everything a walk would otherwise fetch from the network
# is fetched HERE: each git-sourced third-party recipe's upstream is a bare mirror (git-mirrors.txt).

# The LLVM archive key, accepted only when the file holds exactly one primary key, of this fingerprint.
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac AS llvm-key
ARG LLVM_ARCHIVE_FINGERPRINT=6084F3CF814B57C1CF12EFD515CF4D18AF4F7421
ADD https://apt.llvm.org/llvm-snapshot.gpg.key /llvm.asc
RUN apt-get update && apt-get install -y --no-install-recommends gnupg \
 && gpg --batch --show-keys --with-colons /llvm.asc > /llvm.keys \
 && test "$(grep -c '^pub:' /llvm.keys)" = 1 \
 && test "$(awk -F: '$1 == "fpr" { print $10; exit }' /llvm.keys)" = "$LLVM_ARCHIVE_FINGERPRINT" \
 && gpg --batch --dearmor -o /llvm-archive-keyring.gpg /llvm.asc

FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

ARG SNAPSHOT=20261018T000000Z
ARG GCC_ASSET=https://github.com/CodeRoasted/malf-toolchain/releases/download/gcc-16.2.0/gcc-16.2-noble-x64.tar.zst
ARG GCC_SHA256=736331ecc8b4cf433b916b2be5d618b87811cf881ee6b5f0d6bab1cad199a70b

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8

# snapshot.ubuntu.com answers http with a redirect to https, so the certificate store comes first, at
# its pinned version, from the base image's own archive (apt verifies it against the archive key).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates=20260601~24.04.1 \
 && rm -rf /var/lib/apt/lists/* /etc/apt/sources.list.d/ubuntu.sources \
 && printf 'Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/%s\nSuites: noble noble-updates noble-security\nComponents: main universe\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n' "$SNAPSHOT" \
      > /etc/apt/sources.list.d/snapshot.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      binutils=2.42-4ubuntu2.10 \
      bzip2=1.0.8-5.1ubuntu0.1 \
      file=1:5.45-3build1 \
      git=1:2.43.0-1ubuntu7.3 \
      libc6-dev=2.39-0ubuntu8.9 \
      libnuma-dev=2.0.18-1ubuntu0.24.04.1 \
      libnuma1=2.0.18-1ubuntu0.24.04.1 \
      libzstd1=1.5.5+dfsg2-2build1.1 \
      linux-libc-dev=6.8.0-147.147 \
      make=4.3-4.1build2 \
      patchelf=0.18.0-1.1build1 \
      perl=5.38.2-3.2ubuntu0.6 \
      python3.12=3.12.3-1ubuntu0.17 \
      python3.12-venv=3.12.3-1ubuntu0.17 \
      strace=6.8-0ubuntu2 \
      unzip=6.0-28ubuntu4.1 \
      util-linux=2.39.3-9ubuntu6.6 \
      xz-utils=5.6.1+really5.4.5-1ubuntu0.3 \
      zstd=1.5.5+dfsg2-2build1.1 \
 && rm -rf /var/lib/apt/lists/*

ADD --checksum=sha256:${GCC_SHA256} ${GCC_ASSET} /tmp/gcc.tar.zst
RUN tar -C /opt -I zstd -xf /tmp/gcc.tar.zst && rm /tmp/gcc.tar.zst \
 && test -x /opt/gcc-16.2/bin/g++ \
 && ln -sf /opt/gcc-16.2/bin/gcc /usr/bin/cc && ln -sf /opt/gcc-16.2/bin/g++ /usr/bin/c++ \
 && ln -sf /opt/gcc-16.2/bin/gcc /usr/bin/gcc && ln -sf /opt/gcc-16.2/bin/g++ /usr/bin/g++

COPY bootstrap.txt requirements.txt /opt/build-environment/
RUN python3.12 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/build-environment/bootstrap.txt \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-build-isolation -r /opt/build-environment/requirements.txt

ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The clang-21 legs (DN-142.D30 (5) E2a): the compiler, libc++ and its ABI library (the `import std` legs),
# the sanitizer runtimes, clang-tidy and clang-format, behind the bare clang/clang++ names as on the desk;
# and the libc++.modules.json bridge: its relative source path resolves to /usr/lib/share/libc++, where the
# package installs nothing (the import-std prerequisite every libc++ profile names).
COPY --from=llvm-key /llvm-archive-keyring.gpg /usr/share/keyrings/llvm-archive-keyring.gpg
RUN printf 'Types: deb\nURIs: https://apt.llvm.org/noble/\nSuites: llvm-toolchain-noble-21\nComponents: main\nSigned-By: /usr/share/keyrings/llvm-archive-keyring.gpg\n' \
      > /etc/apt/sources.list.d/llvm.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      clang-21 clang-format-21 clang-tidy-21 libc++-21-dev libc++abi-21-dev libclang-rt-21-dev llvm-21 \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /usr/lib/share && ln -sfn /usr/lib/llvm-21/share/libc++ /usr/lib/share/libc++ \
 && ln -sf /usr/bin/clang-21 /usr/bin/clang && ln -sf /usr/bin/clang++-21 /usr/bin/clang++ \
 && clang++-21 -std=c++23 -stdlib=libc++ -x c++ - -o /dev/null <<'PROBE'
#include <expected>
int main() { return std::expected<int, int>{0}.value(); }
PROBE

# The git mirrors (DN-142.D30 (7) R1): each row of git-mirrors.txt cloned bare, serving any commit id it
# holds, the row's commit required present, and the upstream URL rewritten to it for every git command.
COPY git-mirrors.txt /opt/build-environment/
RUN grep -v '^[[:space:]]*\(#\|$\)' /opt/build-environment/git-mirrors.txt | while read -r url commit; do \
      mirror="/opt/build-environment/git-mirrors/${url#*://}" \
      && git clone --bare --quiet "$url" "$mirror" \
      && git -C "$mirror" config uploadpack.allowAnySHA1InWant true \
      && git -C "$mirror" cat-file -e "${commit}^{commit}" \
      && git config --system "url.file://${mirror}.insteadOf" "$url" \
      || exit 1; \
    done
