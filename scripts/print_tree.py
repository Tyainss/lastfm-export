from pathlib import Path

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".idea",
    ".vscode",
}

EXCLUDE_SUFFIXES = {
    ".egg-info",
}

DEFAULT_TOP_LEVEL = {
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "src",
    "tests",
}


def print_repo_tree(
    root: str | Path = ".",
    *,
    max_depth: int = 10,
    top_level: set[str] | None = None,
    include_src_package: str = "lastfm_export",
) -> None:
    root_path = Path(root).resolve()
    top_level = top_level or set(DEFAULT_TOP_LEVEL)

    def excluded(p: Path) -> bool:
        for part in p.parts:
            if part in EXCLUDE_DIRS:
                return True
            for suf in EXCLUDE_SUFFIXES:
                if part.endswith(suf):
                    return True
        return False

    def should_show_top(p: Path) -> bool:
        return p.name in top_level

    def depth(p: Path) -> int:
        return len(p.relative_to(root_path).parts)

    def iter_children(dir_path: Path) -> list[Path]:
        children = []
        for child in dir_path.iterdir():
            if excluded(child):
                continue
            if depth(child) > max_depth:
                continue
            children.append(child)
        # dirs first, then files; alphabetical
        children.sort(key=lambda x: (x.is_file(), x.name.lower()))
        return children

    def print_dir(dir_path: Path, prefix: str = "") -> None:
        children = iter_children(dir_path)
        for i, child in enumerate(children):
            is_last = i == (len(children) - 1)
            branch = "└── " if is_last else "├── "
            print(prefix + branch + child.name + ("/" if child.is_dir() else ""))

            if child.is_dir():
                next_prefix = prefix + ("    " if is_last else "│   ")
                print_dir(child, prefix=next_prefix)

    # Root
    print(f"{root_path.name}/")

    # Top-level curated: only show selected entries
    top_children = []
    for child in root_path.iterdir():
        if excluded(child):
            continue
        if not should_show_top(child):
            continue
        top_children.append(child)
    top_children.sort(key=lambda x: (x.is_file(), x.name.lower()))

    # Print top-level entries; for src, only expand src/lastfm_export
    for i, child in enumerate(top_children):
        is_last_top = i == (len(top_children) - 1)
        branch = "└── " if is_last_top else "├── "
        print(branch + child.name + ("/" if child.is_dir() else ""))

        if not child.is_dir():
            continue

        if child.name == "src":
            pkg = child / include_src_package
            if pkg.exists() and pkg.is_dir():
                next_prefix = "    " if is_last_top else "│   "
                print(next_prefix + "└── " + include_src_package + "/")
                print_dir(pkg, prefix=next_prefix + "    ")
            continue

        # For tests/, expand normally
        next_prefix = "    " if is_last_top else "│   "
        print_dir(child, prefix=next_prefix)


if __name__ == "__main__":
    print_repo_tree()