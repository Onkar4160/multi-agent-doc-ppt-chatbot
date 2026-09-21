"""Artifact versioning service for immutable version management and disk storage."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Sequence

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact, ArtifactVersion

logger = logging.getLogger(__name__)


async def create_artifact(
    db: AsyncSession,
    kind: str,
    title: str,
) -> Artifact:
    """Create a new top-level Artifact record."""
    clean_type = "pptx" if kind.lower() == "pptx" else "docx"
    artifact = Artifact(
        title=title,
        artifact_type=clean_type,
    )
    db.add(artifact)
    await db.flush()
    return artifact


async def add_version(
    db: AsyncSession,
    artifact_id: int,
    model_json: str,
    source_file_path: str | Path,
    change_summary: str | None = "Initial generation",
    source_ids: list[int] = [],
    parent_version_id: int | None = None,
    diff_json: str | dict | list | None = None,
) -> ArtifactVersion:
    """Add a new version to an existing artifact and persist the rendered file to storage/artifacts/<artifact_id>/v<N>.<ext>."""
    src_path = Path(source_file_path)
    if not src_path.exists():
        raise FileNotFoundError(f"Rendered file not found: {source_file_path}")

    # Determine auto-incremented version_no
    res = await db.execute(
        select(func.max(ArtifactVersion.version_no)).where(ArtifactVersion.artifact_id == artifact_id)
    )
    current_max = res.scalar() or 0
    new_version_no = current_max + 1

    ext = src_path.suffix.lower()
    dest_dir = Path("storage/artifacts") / str(artifact_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"v{new_version_no}{ext}"

    # Copy rendered file to versioned artifact store
    shutil.copy2(src_path, dest_path)

    d_json_str = diff_json if isinstance(diff_json, str) or diff_json is None else json.dumps(diff_json)

    version_record = ArtifactVersion(
        artifact_id=artifact_id,
        version_no=new_version_no,
        model_json=model_json,
        file_path=str(dest_path),
        file_type=ext.lstrip("."),
        parent_version_id=parent_version_id,
        change_summary=change_summary,
        source_ids_json=json.dumps(source_ids),
        diff_json=d_json_str,
    )
    db.add(version_record)
    await db.flush()

    logger.info("Added Artifact %d Version %d saved at %s", artifact_id, new_version_no, dest_path)
    return version_record


async def list_artifacts(db: AsyncSession) -> Sequence[Artifact]:
    """List all artifacts."""
    result = await db.execute(select(Artifact).order_by(Artifact.id.desc()))
    return result.scalars().all()


async def list_versions(db: AsyncSession, artifact_id: int) -> Sequence[ArtifactVersion]:
    """List all versions for a given artifact."""
    result = await db.execute(
        select(ArtifactVersion)
        .where(ArtifactVersion.artifact_id == artifact_id)
        .order_by(ArtifactVersion.version_no.asc())
    )
    return result.scalars().all()


async def get_version(
    db: AsyncSession,
    artifact_id: int,
    version_no: int | None = None,
) -> ArtifactVersion | None:
    """Retrieve a specific version or the latest version if version_no is None."""
    query = select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact_id)
    if version_no is not None:
        query = query.where(ArtifactVersion.version_no == version_no)
    else:
        query = query.order_by(ArtifactVersion.version_no.desc())

    result = await db.execute(query)
    return result.scalars().first()
