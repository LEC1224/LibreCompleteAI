from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "extension"
DIST_DIR = ROOT / "dist"
OUTPUT = DIST_DIR / "LibreCompleteAI.oxt"
LEGACY_OUTPUTS = (DIST_DIR / "writer-autocomplete.oxt",)


def main():
    if not EXTENSION_DIR.exists():
        raise SystemExit("Missing extension directory")

    DIST_DIR.mkdir(exist_ok=True)
    for output in (OUTPUT,) + LEGACY_OUTPUTS:
        if output.exists():
            output.unlink()

    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXTENSION_DIR.rglob("*")):
            if path.is_file() and _should_package(path):
                archive.write(path, path.relative_to(EXTENSION_DIR).as_posix())

    print(f"Built {OUTPUT}")


def _should_package(path):
    parts = set(path.parts)
    if "__pycache__" in parts:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


if __name__ == "__main__":
    main()
