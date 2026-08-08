"""Resolve and freeze exactly one industry pack per crawl.

The boundary between the mutable project setting and the immutable analysis
record. A crawl resolves its pack ONCE at creation, freezes the exact manifest
(catalog version, pack id/version, content hash, classifier version) into
``SiteCrawl.configuration``, and every later read renders that frozen manifest.

Nothing here runs per page: the worker loads the frozen manifest and compiles
the pack once per process (see ``compiled_pack_for_manifest``), so the hot loop
performs no file I/O, no hashing, and no catalog lookup.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import lru_cache

from app.analysis.site_health.knowledge import (
    KnowledgeVocabulary,
    compile_vocabulary,
)
from app.core.config.industry_packs.catalog import (
    CatalogError,
    load_pack,
    pack_manifest,
    resolve_pack_id,
)
from app.core.config.industry_packs.reference import (
    CompiledPack,
    classify_page,
    compile_pack,
)
from app.core.config.site_health import (
    CORPUS_DISPOSITION_ANALYZE,
    INDUSTRY_PACK_MANIFEST_KEY,
    TEMPORAL_STATE_UNKNOWN,
)

logger = logging.getLogger("app.domain.site_health.industry_pack")

# Matches ``SitePageAnalysis.industry_role_id`` / secondary role id widths.
_MAX_ROLE_ID_CHARS = 64


class IndustryPackError(RuntimeError):
    """The frozen pack cannot be loaded exactly as recorded."""


def resolve_project_pack_id(project) -> str:
    """Resolve a project's canonical pack id, or "" when none applies.

    Prefers the explicitly stored canonical id. Falls back to resolving the
    onboarding subindustry/industry LABEL — but only at this write-time
    boundary, never on a read path, and never through the general fallback: an
    unknown or ambiguous label leaves the project unpacked so Site Intelligence
    runs generically instead of asserting a pack the user never chose.
    """

    explicit = str(getattr(project, "industry_pack_id", "") or "").strip()
    if explicit:
        return explicit
    for label in (
        str(getattr(project, "subindustry", "") or "").strip(),
        str(getattr(project, "industry", "") or "").strip(),
    ):
        if not label:
            continue
        try:
            return resolve_pack_id(label, allow_general_fallback=False)
        except CatalogError:
            continue
    return ""


def freeze_pack_manifest(pack_id: str) -> dict[str, str] | None:
    """The exact manifest to store on a crawl, or ``None`` when unpacked.

    Resolution failure is deliberately non-fatal here: a crawl must still run
    and produce generic Site Health evidence when no pack applies. What must
    never happen is a crawl claiming a pack it did not actually load.
    """

    if not pack_id:
        return None
    try:
        entry_version = _registered_version(pack_id)
        return dict(pack_manifest(pack_id, entry_version))
    except CatalogError:
        logger.warning(
            "industry pack could not be frozen; crawl runs generically",
            extra={"pack_id": pack_id},
        )
        return None


def _registered_version(pack_id: str) -> str:
    from app.core.config.industry_packs.catalog import registry

    versions = [
        str(entry["version"])
        for entry in registry()["packs"]
        if entry["pack_id"] == pack_id
    ]
    if len(versions) != 1:
        raise CatalogError(f"registry must contain exactly one active {pack_id}")
    return versions[0]


def frozen_manifest(configuration: Mapping | None) -> dict[str, str] | None:
    """Read the frozen manifest off a crawl configuration."""

    manifest = (configuration or {}).get(INDUSTRY_PACK_MANIFEST_KEY)
    if not isinstance(manifest, Mapping):
        return None
    pack_id = str(manifest.get("pack_id") or "")
    pack_version = str(manifest.get("pack_version") or "")
    if not pack_id or not pack_version:
        return None
    return {str(key): str(value) for key, value in manifest.items()}


def _load_verified(pack_id: str, version: str, content_hash: str):
    """One exact pack plus its observed manifest, verified against the freeze.

    ``load_pack`` verifies the hash itself; the comparison here catches the case
    where the registry was re-released under the same id/version with different
    bytes, which would silently change a historical crawl's meaning.

    Shared by the compile and vocabulary caches so the two can never disagree
    about whether a pack is still the one a crawl froze — they were the same
    block of code twice, and a fix to one would have missed the other.
    """
    pack = load_pack(pack_id, version)
    observed = dict(pack_manifest(pack_id, version))
    if content_hash and observed.get("pack_content_hash") != content_hash:
        raise IndustryPackError(
            f"frozen pack hash no longer matches the catalog: {pack_id}@{version}"
        )
    return pack, observed


@lru_cache(maxsize=16)
def _compiled(pack_id: str, version: str, content_hash: str) -> CompiledPack:
    """Compile one exact pack, verifying it is byte-identical to the freeze.

    The cache key includes the content hash so a catalog edit can never be
    served from a stale compile.
    """

    pack, observed = _load_verified(pack_id, version, content_hash)
    return compile_pack(pack, manifest=observed)


def build_role_facts(
    facts: Mapping,
    *,
    page_kind: str,
    final_url: str,
    corpus_disposition: str,
) -> dict[str, object]:
    """Map extractor output onto the reference classifier's bounded fields.

    Deliberately never invents an empty string as a positive fact: a missing
    title stays missing rather than becoming "", which would otherwise match a
    signal looking for an empty value. Full HTML is never copied — only the
    bounded fields the classifier actually scores.
    """

    headings = facts.get("headings") or {}
    body = facts.get("body") or {}
    structured = facts.get("structured_data") or {}
    heading_texts = [
        *(headings.get("h2_texts") or []),
        *(headings.get("h3_texts") or []),
    ]
    h1_texts = headings.get("h1_texts") or []
    return {
        "url": final_url,
        "title": facts.get("title") or None,
        "h1": (h1_texts[0] if h1_texts else None),
        "headings": heading_texts,
        "body": body.get("text") or None,
        "cta_text": facts.get("cta_text") or (),
        "form_fields": facts.get("form_fields") or (),
        "link_context": facts.get("link_context") or (),
        "schema_types": structured.get("types") or (),
        "media_type": facts.get("content_type") or None,
        "page_kind": page_kind,
        "corpus_disposition": corpus_disposition,
    }


def _checked_role_id(role_id: object, manifest: Mapping[str, str]) -> str | None:
    """Return a role ID that fits the column, or ``None`` with a loud log.

    Truncating would persist a DIFFERENT identifier that silently fails every
    later lookup and join — a wrong role reads as a real finding, where a
    missing one reads as "not classified". Dropping it is the honest failure,
    and the log names the pack so the catalog can be corrected.
    """
    if role_id is None:
        return None
    text = str(role_id)
    if len(text) <= _MAX_ROLE_ID_CHARS:
        return text
    logger.error(
        "industry pack role id exceeds the persisted column width; dropping it",
        extra={
            "pack_id": manifest.get("pack_id", ""),
            "pack_version": manifest.get("pack_version", ""),
            "role_id_length": len(text),
            "maximum": _MAX_ROLE_ID_CHARS,
        },
    )
    return None


def classify_industry_role(
    *,
    crawl,
    facts: Mapping,
    page_kind: str,
    site_url,
) -> dict[str, object]:
    """Classify one page's industry role into ``SitePageAnalysis`` columns.

    Returns column values only, so the caller owns persistence. An unpacked
    crawl yields empty manifest fields and a NULL role with NO abstention
    reason — the "classifier never ran" state, which readers must be able to
    tell apart from an executed abstention.
    """

    disposition = str(
        getattr(site_url, "corpus_disposition", "") or CORPUS_DISPOSITION_ANALYZE
    )
    manifest = frozen_manifest(crawl.configuration)
    compiled = compiled_pack_for_manifest(manifest)
    # ``compiled`` is None exactly when ``manifest`` is missing/incomplete, but
    # the two are checked together so the manifest reads below are provably
    # non-null rather than relying on that coupling holding.
    if compiled is None or manifest is None:
        return {
            "corpus_disposition": disposition,
            "temporal_state": TEMPORAL_STATE_UNKNOWN,
        }

    final_url = str((facts.get("delivery") or {}).get("final_url") or "")
    result = classify_page(
        compiled,
        build_role_facts(
            facts,
            page_kind=page_kind,
            final_url=final_url,
            corpus_disposition=disposition,
        ),
    )
    primary_role_id = _checked_role_id(result.get("primary_role_id"), manifest)
    return {
        "industry_role_id": primary_role_id,
        "industry_role_score": result.get("primary_score"),
        "industry_role_margin": result.get("winner_margin"),
        "industry_role_confidence": str(result.get("confidence_band") or "")[:16],
        # Empty string (not NULL) when the classifier committed to a role, so
        # "abstained" stays distinguishable from "never ran".
        "role_abstention_reason": str(result.get("abstention_reason") or "")[:32],
        "industry_role_evidence": {
            "evidence": result.get("evidence") or [],
            "alternatives": result.get("alternatives") or [],
            "conflicts": result.get("conflicts") or [],
        },
        "secondary_role_ids": [
            checked
            for role in (result.get("secondary_role_ids") or [])
            if (checked := _checked_role_id(role, manifest)) is not None
        ],
        "industry_pack_id": str(manifest.get("pack_id", ""))[:64],
        "industry_pack_version": str(manifest.get("pack_version", ""))[:32],
        "pack_content_hash": str(manifest.get("pack_content_hash", ""))[:64],
        "catalog_version": str(manifest.get("catalog_version", ""))[:32],
        "role_classifier_version": str(manifest.get("classifier_version", ""))[:64],
        "corpus_disposition": disposition,
        "temporal_state": str(result.get("temporal_state") or TEMPORAL_STATE_UNKNOWN)[
            :16
        ],
    }


@lru_cache(maxsize=16)
def _vocabulary(pack_id: str, version: str, content_hash: str) -> KnowledgeVocabulary:
    """Compile one exact pack's knowledge vocabulary (same freeze guarantees).

    Cached on the same key as the role classifier's compile, so a catalog edit
    can never be served from a stale vocabulary and a crawl's knowledge is
    always built against the pack it froze.
    """

    pack, _observed = _load_verified(pack_id, version, content_hash)
    return compile_vocabulary(pack)


def knowledge_vocabulary_for_manifest(
    manifest: Mapping[str, str] | None,
) -> KnowledgeVocabulary | None:
    """The crawl's frozen knowledge vocabulary, or ``None`` when unpacked.

    ``None`` is the "no pack ever applied" state, and callers must keep it
    distinct from an empty vocabulary: a crawl with no pack produces no
    knowledge because nothing defined what knowledge would mean, which is not
    the same finding as a site that published none.
    """

    if not manifest:
        return None
    pack_id = str(manifest.get("pack_id") or "")
    version = str(manifest.get("pack_version") or "")
    if not pack_id or not version:
        return None
    try:
        return _vocabulary(
            pack_id, version, str(manifest.get("pack_content_hash") or "")
        )
    except CatalogError as exc:
        raise IndustryPackError(str(exc)) from exc


def compiled_pack_for_manifest(
    manifest: Mapping[str, str] | None,
) -> CompiledPack | None:
    """Compile the crawl's frozen pack, or ``None`` when it is unpacked.

    Raises ``IndustryPackError`` when a manifest names a pack that cannot be
    loaded exactly: that is an operational failure worth surfacing, not a
    reason to substitute a newer pack or fall back to a general one.
    """

    if not manifest:
        return None
    pack_id = str(manifest.get("pack_id") or "")
    version = str(manifest.get("pack_version") or "")
    if not pack_id or not version:
        return None
    try:
        return _compiled(pack_id, version, str(manifest.get("pack_content_hash") or ""))
    except CatalogError as exc:
        raise IndustryPackError(str(exc)) from exc
