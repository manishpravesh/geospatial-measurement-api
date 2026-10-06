"""Zip handling for shapefile archives and KMZ.

Uploads are untrusted. Member names are checked before anything is written,
and a shapefile zip only contributes the sidecars the format actually needs.
"""

import zipfile
from pathlib import Path

from app.exceptions import RejectedUpload

SHAPEFILE_SUFFIXES = {
    ".shp",
    ".shx",
    ".dbf",
    ".prj",
    ".cpg",
    ".qpj",
    ".sbn",
    ".sbx",
    ".qix",
}


def is_unsafe_member(name: str) -> bool:
    if not name or len(name) > 240:
        return True
    if name.startswith(("/", "\\")):
        return True
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts:
        return True
    for part in parts:
        if part == ".." or ":" in part:
            return True
    return False


def is_junk_member(name: str) -> bool:
    parts = [part.lower() for part in name.replace("\\", "/").split("/") if part]
    return any(part in {"__macosx", ".ds_store", "thumbs.db"} for part in parts)


def is_shapefile_part(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".shp.xml"):
        return True
    return Path(lower).suffix in SHAPEFILE_SUFFIXES


def is_kml_member(name: str) -> bool:
    return Path(name.lower()).suffix == ".kml"


def _member_target(root: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    target = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise RejectedUpload("Archive contains a path that escapes the upload directory.")
    return target


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with archive.open(info, "r") as src, target.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > budget:
                raise RejectedUpload("Archive expands past the size limit.")
            dst.write(chunk)
    return written


def extract_members(
    zip_path: Path,
    dest: Path,
    *,
    predicate,
    max_unzipped_bytes: int,
    max_members: int,
) -> list[Path]:
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise RejectedUpload("The archive is not a valid zip file.") from exc

    with archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > max_members:
            raise RejectedUpload(f"Archive has too many files (limit {max_members}).")

        declared = 0
        for info in members:
            if info.flag_bits & 0x1:
                raise RejectedUpload("Encrypted archives are not supported.")
            if is_unsafe_member(info.filename):
                raise RejectedUpload("Archive contains a path that escapes the upload directory.")
            declared += max(info.file_size, 0)
        if declared > max_unzipped_bytes:
            raise RejectedUpload("Archive expands past the size limit.")

        written: list[Path] = []
        budget = max_unzipped_bytes
        for info in members:
            if is_junk_member(info.filename) or not predicate(info.filename):
                continue
            target = _member_target(dest, info.filename)
            used = _copy_member(archive, info, target, budget)
            budget -= used
            written.append(target)
        return written


def _find_sibling(shp_path: Path, suffix: str) -> Path | None:
    wanted = suffix.lower()
    for candidate in shp_path.parent.iterdir():
        if candidate.stem.lower() == shp_path.stem.lower() and candidate.suffix.lower() == wanted:
            return candidate
    return None


def select_shapefile(shp_paths: list[Path]) -> Path:
    if not shp_paths:
        raise RejectedUpload(
            "The zip does not contain a shapefile. Include the .shp, .shx, and .dbf files."
        )
    if len(shp_paths) > 1:
        names = ", ".join(sorted(path.name for path in shp_paths))
        raise RejectedUpload(
            f"The zip contains more than one shapefile ({names}). Upload one shapefile at a time."
        )

    chosen = shp_paths[0]
    missing = [suffix for suffix in (".shx", ".dbf") if _find_sibling(chosen, suffix) is None]
    if missing:
        joined = " and ".join(missing)
        raise RejectedUpload(
            f"Shapefile {chosen.name} is missing {joined}. "
            "A shapefile needs its .shp, .shx, and .dbf together."
        )
    return chosen


def extract_shapefile(
    zip_path: Path,
    dest: Path,
    *,
    max_unzipped_bytes: int,
    max_members: int,
) -> Path:
    written = extract_members(
        zip_path,
        dest,
        predicate=is_shapefile_part,
        max_unzipped_bytes=max_unzipped_bytes,
        max_members=max_members,
    )
    shapefiles = [path for path in written if path.suffix.lower() == ".shp"]
    return select_shapefile(shapefiles)


def extract_kml_from_kmz(
    zip_path: Path,
    dest: Path,
    *,
    max_unzipped_bytes: int,
    max_members: int,
) -> Path:
    written = extract_members(
        zip_path,
        dest,
        predicate=is_kml_member,
        max_unzipped_bytes=max_unzipped_bytes,
        max_members=max_members,
    )
    if not written:
        raise RejectedUpload("KMZ archive does not contain a KML file.")
    preferred = [path for path in written if path.name.lower() == "doc.kml"]
    if preferred:
        return preferred[0]
    if len(written) == 1:
        return written[0]
    names = ", ".join(sorted(path.name for path in written))
    raise RejectedUpload(
        f"KMZ archive contains several KML files ({names}). Expected a single doc.kml."
    )
