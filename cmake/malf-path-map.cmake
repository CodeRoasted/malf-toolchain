# malf-path-map.cmake — every conan CMake build maps its source and build directories out of the
# file names the preprocessor writes, so a package's bytes do not depend on where it was built, and
# links every executable and shared object with a GNU build-id, so each names itself by its bytes.
#
# Attached through global.conf's `tools.cmake.cmaketoolchain:user_toolchain`, which conan includes
# at the top of every conan_toolchain.cmake; staged beside global.conf in each conan home.
#
# WHY (ROADMAP N366, DN-142.D4 (a)). `__FILE__`, `__builtin_FILE()` and `std::source_location`
# expand to the absolute path of the translation unit, and a `conan create` builds in
# `<conan home>/p/b/<name><12 hex>/b`: the home's path, and a folder name drawn per create. So two
# creates of one commit published different bytes — 9 of the 20 released packages, measured in one
# home, the folder name the sole cause, verified by substitution.
#
# WHY HERE AND NOT IN THE PROFILE. The folder is unknown when a profile is written, and a path in
# `tools.build:cxxflags` enters the package id (global.conf folds cxxflags into it), so the id
# itself would depend on the home. This file names no path: CMake knows both directories at
# configure time. `-fmacro-prefix-map` and not `-ffile-prefix-map`: the second also rewrites debug
# information, and a desk Debug build must keep the real paths its debugger opens.
#
# WHY THE *_FLAGS_INIT AND NEVER add_compile_options. A directory compile option becomes every
# target's COMPILE_OPTIONS, and CMake EXPORTS a module target's into
# `IMPORTED_CXX_MODULES_COMPILE_OPTIONS`: measured 2026-10-09, every released module package then
# shipped the producer's build folder inside its package config — the relocation defect ROADMAP
# N366 exists to catch, re-made by its own fix. The language flags are never exported.
#
# THE CONAN HOME TOO, BECAUSE A DEPENDENCY'S HEADER IS UNDER IT. A header a package includes from
# another one sits at `<conan home>/p/<folder>/p/include`, and its `__FILE__` lands in the bytes:
# measured 2026-10-09, coderoast_infra_postgres differed across two homes ONLY by libpqxx's
# `params.hxx` and `transaction.hxx` paths, after the source and build maps had made the other 19
# packages identical. This file is staged IN the home it serves, so its own directory is the home.
# gcc tries the LAST map first: the home goes first, so the build folder, under it, maps as itself.
#
# GNU and Clang only. A toolchain file runs before the compiler is known, so the gate is the host:
# a Windows build here is MSVC, which has no such flag, and DN-142.D4 (a) declares it not covered.
if(CMAKE_HOST_WIN32)
    return()
endif()
include_guard(GLOBAL)

foreach(language IN ITEMS C CXX)
    string(APPEND CMAKE_${language}_FLAGS_INIT
           " -fmacro-prefix-map=${CMAKE_CURRENT_LIST_DIR}=conan-home"
           " -fmacro-prefix-map=${CMAKE_SOURCE_DIR}=. -fmacro-prefix-map=${CMAKE_BINARY_DIR}=build")
endforeach()

# EVERY LINKED ELF OUTPUT NAMES ITSELF BY ITS OWN BYTES (DN-142.D15). `--build-id=sha1` writes a
# 20-byte NT_GNU_BUILD_ID note the linker computes over the output, so equal inputs give an equal
# id, it survives `strip`, and a core dump, `gdb` and `readelf -n` read it. The store indexes every
# ELF file a first-party package installs by it (artefact_store.py `build_ids`), and the server's
# /ready reports its own. A binary carries no git state, path or time instead: those are the seat's,
# never the bytes'. The workspace gcc-16.2 links no note by default (measured: none in the M1
# binaries). Linker flags, never compile flags: they are not package_id inputs and not exported.
foreach(kind IN ITEMS EXE SHARED MODULE)
    string(APPEND CMAKE_${kind}_LINKER_FLAGS_INIT " -Wl,--build-id=sha1")
endforeach()
