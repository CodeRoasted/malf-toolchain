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
# ninja, PyYAML, the jsonschema closure). The clang-21 legs are NOT in it: the desk takes them from
# apt.llvm.org, which no snapshot serves.
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
