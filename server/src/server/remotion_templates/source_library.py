"""Shared immutable component sources, archived atomically with accepted generation and reused by copying.

This library is independent of both private works and published Sprite assets. It contains only accepted
Effect.tsx/effect.json pairs and generated descriptions, never conversations, reference images or failed drafts.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime

import jieba
from sqlalchemy import Connection, func, insert, or_, select

from ..database import get_engine
from .tables import effect_sources
from .tools.contracts import SourceReadInput, SourceSearchInput, SourceSearchOutput, SourceSummary, WorkspaceFile
from .tools.registry import ToolFault
from .tools.workspace import file_page, restore_version_files

jieba.setLogLevel(logging.WARNING)


def archive(db: Connection, version) -> str:
    """Deduplicate exact accepted source/manifest bytes; the generation publication transaction owns the write."""
    files = restore_version_files(version.spec, version.candidate)
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    identifier = hashlib.sha256(payload.encode()).hexdigest()
    if db.execute(select(effect_sources.c.id).where(effect_sources.c.id == identifier)).first() is None:
        manifest = json.loads(files["effect.json"])
        db.execute(insert(effect_sources).values(
            id=identifier, description=manifest["description"], kind=manifest["kind"], files=payload,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        ))
    return identifier


def search(request: SourceSearchInput) -> SourceSearchOutput:
    """Filter Chinese/English appearance terms in SQL and cap encoded summaries independently of source size."""
    engine = get_engine()
    effect_sources.create(engine, checkfirst=True)
    words = {word.strip().lower() for word in jieba.cut_for_search(request.query) if any(char.isalnum() for char in word)}
    terms = sorted({word for word in words if len(word) > 1} or words)[:32]
    query = select(effect_sources.c.id, effect_sources.c.description, effect_sources.c.kind)
    if terms:
        query = query.where(or_(*(func.lower(effect_sources.c.description).contains(term, autoescape=True) for term in terms)))
    with engine.connect() as db:
        rows = db.execute(query.order_by(effect_sources.c.created_at.desc(), effect_sources.c.id).limit(request.limit + 1)).all()
    result = SourceSearchOutput(sources=[], has_more=len(rows) > request.limit)
    for row in rows[:request.limit]:
        item = SourceSummary(source_id=row.id, description=row.description[:800], kind=row.kind)
        result.sources.append(item)
        if len(result.model_dump_json().encode()) > 32_000:
            result.sources.pop()
            result.has_more = True
            break
    return result


def read(request: SourceReadInput) -> WorkspaceFile:
    """Read only the two declared immutable files; IDs and offsets cannot address host files or render evidence."""
    engine = get_engine()
    effect_sources.create(engine, checkfirst=True)
    with engine.connect() as db:
        payload = db.execute(select(effect_sources.c.files).where(effect_sources.c.id == request.source_id)).scalar()
    if payload is None:
        raise ToolFault("SOURCE_NOT_FOUND", "Shared source not found.")
    return file_page(request.path, json.loads(payload)[request.path], request.offset)
