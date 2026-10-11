# writer.profile — the layer every malf writer act (store-create, store-build) composes over its
# profile, host and build alike, and nothing else does (DN-142.D17 (8), commit B). A build compiles
# through malf's wrapper, reached by NAME on the PATH below, so a build that records its compiler
# records `malf-cc`, never a path of the seat; the act exports the real compilers it wraps
# (MALF_REAL_CC, MALF_REAL_CXX, read from the profile's own CC and CXX). Not a package-id input: a
# [buildenv] never is. Its content and the wrapper's enter every key through `definition` (malf's
# tree), and the rendered layer through `profile`.
[buildenv]
CC=malf-cc
CXX=malf-c++
PATH=+(path){{ os.path.join(os.path.dirname(profile_dir), "wrap") }}
