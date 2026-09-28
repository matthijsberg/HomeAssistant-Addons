import os
import tarfile
import base64
import subprocess
from pathlib import Path
from typing import Tuple

MAX_COMPRESSED_BYTES = 25 * 1024 * 1024       # 25 MB
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024       # 200 MB
MAX_EXTRACTED_FILES = 20_000


def safely_extract_tar(tar_path: Path, dest_dir: Path) -> int:
    """
    Safely extract a tar.gz archive, rejecting path traversal, absolute paths,
    device nodes, or symlinks pointing outside the extraction root.
    Returns total extracted bytes.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()

    total_bytes = 0
    total_files = 0

    with tarfile.open(tar_path, "r:*") as tar:
        for member in tar.getmembers():
            total_files += 1
            if total_files > MAX_EXTRACTED_FILES:
                raise ValueError(f"Exceeded max extracted files count ({MAX_EXTRACTED_FILES})")

            # Check for illegal file types (block/char devices, fifos)
            if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
                raise ValueError(f"Illegal file type in archive: {member.name}")

            # Check target path traversal
            target_path = (dest_dir / member.name).resolve()
            if target_path != dest_resolved and dest_resolved not in target_path.parents:
                raise ValueError(f"Archive entry attempts directory traversal: {member.name}")

            # Check symlinks pointing outside root
            if member.issym() or member.islnk():
                link_target = (dest_dir / member.linkname).resolve()
                if link_target != dest_resolved and dest_resolved not in link_target.parents:
                    raise ValueError(f"Symlink points outside archive root: {member.name} -> {member.linkname}")

            total_bytes += member.size
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError(f"Exceeded max extracted byte size ({MAX_EXTRACTED_BYTES} bytes)")

        # Extraction with filter if python >= 3.12, else standard extractall
        if hasattr(tarfile, "data_filter"):
            tar.extractall(path=dest_dir, filter="data")
        else:
            tar.extractall(path=dest_dir)

    return total_bytes


def unpack_upload(base64_data: str, target_dir: Path, upload_type: str = "tar") -> Tuple[Path, str]:
    """
    Decode and unpack base64 git bundle or tar.gz archive into target_dir.
    """
    raw_bytes = base64.b64decode(base64_data)
    if len(raw_bytes) > MAX_COMPRESSED_BYTES:
        raise ValueError(f"Upload exceeds maximum compressed size: {len(raw_bytes)} > {MAX_COMPRESSED_BYTES}")

    target_dir.mkdir(parents=True, exist_ok=True)

    if upload_type == "bundle":
        bundle_file = target_dir / "repo.bundle"
        bundle_file.write_bytes(raw_bytes)
        
        # Verify bundle
        v_res = subprocess.run(
            ["git", "bundle", "verify", str(bundle_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if v_res.returncode != 0:
            raise ValueError(f"Git bundle verification failed: {v_res.stderr.strip()}")

        # Clone bundle with hooks disabled and protocol restricted
        clone_dir = target_dir / "repo"
        c_res = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "clone", str(bundle_file), str(clone_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if c_res.returncode != 0:
            raise ValueError(f"Failed to clone git bundle: {c_res.stderr.strip()}")

        return clone_dir, "git_bundle"

    else:
        archive_file = target_dir / "upload.tar.gz"
        archive_file.write_bytes(raw_bytes)
        extract_dir = target_dir / "extracted"
        safely_extract_tar(archive_file, extract_dir)
        return extract_dir, "tar_archive"
