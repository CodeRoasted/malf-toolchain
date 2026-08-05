#!/usr/bin/env python3
"""Workspace conan-recipe graph for malf — the single recipe parser + dep walker.

One AST parse of every `conanfile.py` (no conan invocation, no recipe execution)
feeding three query modes. All modes emit tab-separated `name/version<TAB>dir`
lines on stdout.

  deps <workspace_root> <target_dir> <include_test_requires 0|1>
      Every workspace recipe the target's recipe transitively requires,
      dependency order, target excluded. Regular requires walk transitively;
      the TARGET's test_requires are extra roots (when enabled) but are never
      followed transitively — a dependency's test_requires belong to *its*
      test build, not ours. A test_requires-only editable (e.g. the e2e
      harness's scenario corpus) is otherwise never rebuilt, so an integration
      gate would silently link a stale upstream.

  all <workspace_root>
      EVERY workspace recipe, path-sorted — what `malf editables sync`
      re-registers so a coordinated version bump propagates in one pass.

  members <root_dir>
      Every recipe under root_dir, topologically sorted by the
      requires/test_requires edges BETWEEN those members (dependencies first,
      path-sorted among independents) — the `malf <verb>` no-arg multi-package
      sweep order.
"""

import ast
import pathlib
import sys

IGNORE_PARTS = {".conan2", ".git", ".venv", "node_modules", "test_package"}


def should_skip(path: pathlib.Path) -> bool:
    # IGNORE_PARTS + "build" + every profile-named tree (build-gcc15-release, …).
    return any(part in IGNORE_PARTS or part == "build" or part.startswith("build-")
               for part in path.parts)


def recipe_info(conanfile: pathlib.Path):
    """(name, version, requires, test_requires) from one recipe, by AST only.

    Catches both declaration styles: the class attributes
    (`requires = "pkg/1.0"` / `requires = [...]`) and the method calls
    (`self.requires("pkg/1.0")` / `self.test_requires(...)`). Regular requires
    propagate to consumers; test_requires do not (conan's `test` trait), so
    they are bucketed separately and the walkers decide how far each kind goes.
    """
    tree = ast.parse(conanfile.read_text(), filename=str(conanfile))
    nodes = list(tree.body)
    for item in tree.body:
        if isinstance(item, ast.ClassDef):
            nodes.extend(item.body)

    name = None
    version = None
    requires = []
    test_requires = []

    def add(kind, value):
        (test_requires if kind == "test_requires" else requires).append(value)

    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in ("name", "version"):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    if target.id == "name":
                        name = node.value.value
                    else:
                        version = node.value.value
            elif target.id in ("requires", "test_requires"):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    add(target.id, node.value.value)
                elif isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            add(target.id, elt.value)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in ("requires", "test_requires"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            add(node.func.attr, arg.value)

    return name, version, requires, test_requires


def scan(root: pathlib.Path) -> dict:
    """ref -> {dir, requires, test_requires} for every parsable recipe under root."""
    recipes = {}
    for conanfile in sorted(root.rglob("conanfile.py")):
        if should_skip(conanfile.relative_to(root)):
            continue
        name, version, requires, test_requires = recipe_info(conanfile)
        if not name or not version:
            continue
        recipes[f"{name}/{version}"] = {
            "dir": str(conanfile.parent.resolve()),
            "requires": requires,
            "test_requires": test_requires,
        }
    return recipes


def emit(rows):
    for ref, directory in rows:
        print(f"{ref}\t{directory}")


def resolve_ref(dep: str, recipes: dict) -> str | None:
    """Map a recipe's requirement string onto a workspace member ref, or None.

    An exact `name/version` matches by string. A VERSION RANGE does not, and that
    silently dropped a real edge: `insight_e2e` test_requires
    `insight_scenarios/[>=1.7.0 <2]` while the member index is keyed
    `insight_scenarios/1.9.0`, so `dep in recipes` was False and the corpus package
    was never returned as a dependency — never registered as an editable, and
    therefore unresolvable to its consumer on any checkout without a stale prior
    registration. `mode_deps`' own docstring names that exact case ("a
    test_requires-only editable (e.g. the e2e harness's scenario corpus) is otherwise
    never rebuilt") — the intent was written and the matcher defeated it.

    A range is resolved BY NAME, which is sound here and not a guess: the workspace
    carries exactly one version of each package (the uniform line, held by
    pin_coherence INV-1). That is asserted rather than assumed — an ambiguous name
    raises instead of picking, because silently binding the wrong version would be a
    worse failure than the one this fixes.

    Measured population when this landed: ONE range-form first-party edge in the
    whole workspace. The narrowness is the point — this resolves references, it does
    not widen what counts as a dependency.
    """
    if dep in recipes:
        return dep
    name, _, version = dep.partition("/")
    if "[" not in version:
        return None          # an exact ref that is not a member: third-party, ignore
    matches = [ref for ref in recipes if ref.split("/", 1)[0] == name]
    if not matches:
        return None          # third-party range (openssl/[>=3 <4]) — not ours
    if len(matches) > 1:
        raise SystemExit(
            f"malf_graph: '{dep}' resolves to {len(matches)} workspace members "
            f"({', '.join(sorted(matches))}). The workspace is supposed to carry one "
            "version per package; refusing to guess which one a range means.")
    return matches[0]


def mode_deps(workspace: pathlib.Path, target_dir: pathlib.Path, include_test_requires: bool):
    recipes = scan(workspace)
    t_name, t_version, t_requires, t_test_requires = recipe_info(target_dir / "conanfile.py")
    target_ref = f"{t_name}/{t_version}"
    # The target may live outside the scan (or shadow a same-ref recipe): its own
    # parse is authoritative for the roots of the walk.
    recipes[target_ref] = {
        "dir": str(target_dir.resolve()),
        "requires": t_requires,
        "test_requires": t_test_requires,
    }

    order = []
    seen = set()
    visiting = set()

    def visit(ref: str, is_target: bool = False):
        if ref in seen or ref in visiting:
            return
        visiting.add(ref)
        recipe = recipes.get(ref)
        if recipe:
            deps = list(recipe["requires"])
            if is_target and include_test_requires:
                deps += recipe["test_requires"]
            for dep in deps:
                resolved = resolve_ref(dep, recipes)
                if resolved is not None:
                    visit(resolved)
            if ref != target_ref:
                order.append((ref, recipe["dir"]))
        visiting.remove(ref)
        seen.add(ref)

    visit(target_ref, is_target=True)
    emit(order)


def mode_all(workspace: pathlib.Path):
    recipes = scan(workspace)
    emit(sorted(((ref, info["dir"]) for ref, info in recipes.items()),
                key=lambda row: row[1]))


def mode_members(root: pathlib.Path):
    recipes = scan(root)
    order = []
    seen = set()
    visiting = set()

    def visit(ref: str):
        if ref in seen or ref in visiting:
            return
        visiting.add(ref)
        recipe = recipes[ref]
        for dep in recipe["requires"] + recipe["test_requires"]:
            resolved = resolve_ref(dep, recipes)
            if resolved is not None:
                visit(resolved)
        order.append((ref, recipe["dir"]))
        visiting.remove(ref)
        seen.add(ref)

    for ref in sorted(recipes, key=lambda r: recipes[r]["dir"]):
        visit(ref)
    emit(order)


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write(__doc__)
        return 2
    mode = sys.argv[1]
    if mode == "deps":
        if len(sys.argv) != 5 or sys.argv[4] not in ("0", "1"):
            sys.stderr.write("usage: malf_graph.py deps <workspace_root> <target_dir> <0|1>\n")
            return 2
        mode_deps(pathlib.Path(sys.argv[2]).resolve(),
                  pathlib.Path(sys.argv[3]).resolve(),
                  sys.argv[4] == "1")
    elif mode == "all":
        mode_all(pathlib.Path(sys.argv[2]).resolve())
    elif mode == "members":
        mode_members(pathlib.Path(sys.argv[2]).resolve())
    else:
        sys.stderr.write(f"malf_graph.py: unknown mode '{mode}' (deps|all|members)\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
