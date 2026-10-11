from conan import ConanFile
from conan.tools.env import VirtualBuildEnv
from conan.tools.files import copy, get, rmdir, apply_conandata_patches, replace_in_file, export_conandata_patches
from conan.tools.gnu import Autotools, AutotoolsToolchain
from conan.tools.layout import basic_layout
from conan.tools.microsoft import unix_path, is_msvc
import os
import re
from pathlib import Path

required_conan_version = ">=1.54.0"


class AutoconfConan(ConanFile):
    name = "autoconf"
    description = (
        "Autoconf is an extensible package of M4 macros that produce shell "
        "scripts to automatically configure software source code packages"
    )
    license = ("GPL-2.0-or-later", "GPL-3.0-or-later")
    url = "https://github.com/conan-io/conan-center-index"
    homepage = "https://www.gnu.org/software/autoconf/"
    topics = ("configure", "build")

    package_type = "application"
    settings = "os", "arch", "compiler", "build_type"

    @property
    def _settings_build(self):
        # TODO: Remove for Conan v2
        return getattr(self, "settings_build", self.settings)

    def export_sources(self):
        export_conandata_patches(self)

    def layout(self):
        basic_layout(self, src_folder="src")

    def requirements(self):
        self.requires("m4/1.4.19") # Needed at runtime by downstream clients as well

    def package_id(self):
        del self.info.settings.arch
        del self.info.settings.compiler
        del self.info.settings.build_type
        self.info.requires.clear()

    def build_requirements(self):
        self.tool_requires("m4/1.4.19")
        if self._settings_build.os == "Windows":
            self.win_bash = True
            if not self.conf.get("tools.microsoft.bash:path", check_type=str):
                self.tool_requires("msys2/cci.latest")

    def source(self):
        get(self, **self.conan_data["sources"][self.version], strip_root=True)

    def generate(self):
        env = VirtualBuildEnv(self)
        env.generate()

        tc = AutotoolsToolchain(self)
        tc.configure_args.extend([
            "--datarootdir=${prefix}/res",
        ])

        if self.settings.os == "Windows":
            if is_msvc(self):
                build = "{}-{}-{}".format(
                    "x86_64" if self._settings_build.arch == "x86_64" else "i686",
                    "pc" if self._settings_build.arch == "x86" else "win64",
                    "mingw32")
                host = "{}-{}-{}".format(
                    "x86_64" if self.settings.arch == "x86_64" else "i686",
                    "pc" if self.settings.arch == "x86" else "win64",
                    "mingw32")
                tc.configure_args.append(f"--build={build}")
                tc.configure_args.append(f"--host={host}")

        env = tc.environment()
        env.define_path("INSTALL", unix_path(self, os.path.join(self.source_folder, "build-aux", "install-sh")))
        tc.generate(env)

    def _patch_sources(self):
        apply_conandata_patches(self)
        replace_in_file(self, os.path.join(self.source_folder, "Makefile.in"),
                        "M4 = /usr/bin/env m4", "#M4 = /usr/bin/env m4")
        if self._settings_build.os == "Windows":
            # Handle vagaries of Windows line endings
            replace_in_file(self, os.path.join(self.source_folder, "bin", "autom4te.in"),
                            "$result =~ s/^\\n//mg;", "$result =~ s/^\\R//mg;")

    def build(self):
        self._patch_sources()
        autotools = Autotools(self)
        autotools.configure()
        autotools.make()

    def package(self):
        autotools = Autotools(self)
        autotools.install()

        copy(self, "COPYING*", src=self.source_folder, dst=os.path.join(self.package_folder, "licenses"))
        rmdir(self, os.path.join(self.package_folder, "res", "info"))
        rmdir(self, os.path.join(self.package_folder, "res", "man"))
        # MALF OVERLAY (DN-142.D17 (8)): every frozen m4 state autoconf installs records, in each
        # macro's m4_location, the absolute path m4 opened its source at, under the build folder
        # conan draws per build. The source folder's prefix is dropped from every record, each
        # record's lengths recomputed, so the frozen state names `lib/autoconf/<file>.m4:<line>`.
        for frozen in sorted(Path(self.package_folder, "res", "autoconf").rglob("*.m4f")):
            frozen.write_bytes(_without_prefix(frozen.read_bytes(), (self.source_folder + "/").encode()))

    def package_info(self):
        self.cpp_info.frameworkdirs = []
        self.cpp_info.libdirs = []
        self.cpp_info.includedirs = []
        self.cpp_info.resdirs = ["res"]

        bin_path = os.path.join(self.package_folder, "bin")
        self.buildenv_info.define_path("AUTOCONF", os.path.join(bin_path, "autoconf"))
        self.buildenv_info.define_path("AUTORECONF", os.path.join(bin_path, "autoreconf"))
        self.buildenv_info.define_path("AUTOHEADER", os.path.join(bin_path, "autoheader"))
        self.buildenv_info.define_path("AUTOM4TE", os.path.join(bin_path, "autom4te"))

        # TODO: to remove in conan v2
        self.env_info.PATH.append(bin_path)
        self.env_info.AUTOCONF = "autoconf"
        self.env_info.AUTORECONF = "autoreconf"
        self.env_info.AUTOHEADER = "autoheader"
        self.env_info.AUTOM4TE = "autom4te"


_TWO_LENGTHS = re.compile(rb"([CFQT])(\d+),(\d+)\n")
_DIVERSION = re.compile(rb"D(-?\d+),(\d+)\n")


def _without_prefix(data, prefix):
    """A GNU m4 frozen file with `prefix` removed from every definition, each record re-measured.
    pre: the file is made of `#` comments, `V<n>`, `C`/`F`/`Q`/`T<len>,<len>` and `D<n>,<len>`
         records, each body followed by a newline; any other header raises."""
    out, at = bytearray(), 0
    while at < len(data):
        end = data.index(b"\n", at) + 1
        line = data[at:end]
        two, diversion = _TWO_LENGTHS.fullmatch(line), _DIVERSION.fullmatch(line)
        if two:
            first, second = int(two.group(2)), int(two.group(3))
            body = data[end:end + first + second]
            name, value = body[:first].replace(prefix, b""), body[first:].replace(prefix, b"")
            out += two.group(1) + b"%d,%d\n" % (len(name), len(value)) + name + value + b"\n"
            at = end + first + second + 1
        elif diversion:
            length = int(diversion.group(2))
            text = data[end:end + length].replace(prefix, b"")
            out += b"D" + diversion.group(1) + b",%d\n" % len(text) + text + b"\n"
            at = end + length + 1
        elif line.startswith(b"#") or re.fullmatch(rb"V\d+\n", line):
            out += line
            at = end
        else:
            raise ValueError(f"an m4 frozen record this rewrite does not know: {line[:40]!r}")
    return bytes(out)
