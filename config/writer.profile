# writer.profile — the layer malf's writer act (store-create) composes over its
# profile, host and build alike, and nothing else does (DN-142.D17 (8), commit B). A build compiles
# through malf's wrapper, reached by NAME on the PATH below, so a build that records its compiler
# records `malf-cc`, never a path of the seat; the act exports the real compilers it wraps
# (MALF_REAL_CC, MALF_REAL_CXX, read from the profile's own CC and CXX). Not a package-id input: a
# [buildenv] never is. Its content and the wrapper's enter every key through `definition` (malf's
# whole tree, DN-142.D29 (2)), and the rendered layer through `profile`.
# It holds a [buildenv] ALONE, never an option, a setting or a package-id conf: the writer layer
# must never move a package id, so store-create's keys and a consumer's resolve agree by
# construction (an option here made step 0's server smoke rebuild coderoast_auth, 2026-10-12).
[buildenv]
CC=malf-cc
CXX=malf-c++
PATH=+(path){{ profile_dir }}/../wrap
# bison's configure bakes the absolute path of the m4 it found into the binary, a path under the
# home; handed the bare name, it keeps it and finds m4 on the PATH its consumers' builds carry.
bison/*:M4=m4
# A build that stamps its own time takes this one instead (openssl's `built on:`, compiled into
# libcrypto.a and every binary that links it): a constant, so two builds of one key, in one home or
# two, produce one byte set. Measured 2026-10-11: openssl 3.6.5 built twice a minute apart read
# `built on: Thu Jan  1 00:00:00 1970 UTC` both times, one content digest (DN-142.D17 (8)).
SOURCE_DATE_EPOCH=0
