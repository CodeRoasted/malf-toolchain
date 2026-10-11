# writer.profile — the layer every malf writer act (store-create, store-build) composes over its
# profile, host and build alike, and nothing else does (DN-142.D17 (8), commit B). A build compiles
# through malf's wrapper, reached by NAME on the PATH below, so a build that records its compiler
# records `malf-cc`, never a path of the seat; the act exports the real compilers it wraps
# (MALF_REAL_CC, MALF_REAL_CXX, read from the profile's own CC and CXX). Not a package-id input: a
# [buildenv] never is. Its content and the wrapper's enter every key through `definition` (malf's
# tree), and the rendered layer through `profile`.
[options]
# openssl's version string carries its compile line, and with zlib that line names zlib's package
# folder (`-I<home>/p/b/zlib<drawn>/p/include`), linked into every consumer of libcrypto. TLS
# compression is off in every client here (and broken by CRIME), so the writer's openssl is built
# without zlib: the recipe's own option, nothing forked (DN-142.D17 (8), measured 2026-10-11).
openssl/*:no_zlib=True

[buildenv]
CC=malf-cc
CXX=malf-c++
PATH=+(path){{ os.path.join(os.path.dirname(profile_dir), "wrap") }}
# bison's configure bakes the absolute path of the m4 it found into the binary, a path under the
# home; handed the bare name, it keeps it and finds m4 on the PATH its consumers' builds carry.
bison/*:M4=m4
# A build that stamps its own time takes this one instead (openssl's `built on:`, compiled into
# libcrypto.a and every binary that links it): a constant, so two builds of one key, in one home or
# two, produce one byte set. Measured 2026-10-11: openssl 3.6.5 built twice a minute apart read
# `built on: Thu Jan  1 00:00:00 1970 UTC` both times, one content digest (DN-142.D17 (8)).
SOURCE_DATE_EPOCH=0
