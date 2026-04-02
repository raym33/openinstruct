from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class KnowledgePaths:
    root: Path
    raw_dir: Path
    wiki_dir: Path
    sources_dir: Path
    concepts_dir: Path
    queries_dir: Path
    outputs_dir: Path
    slides_dir: Path
    figures_dir: Path
    config_path: Path
    manifest_path: Path


def knowledge_paths(root: Path) -> KnowledgePaths:
    workspace = root.expanduser().resolve()
    return KnowledgePaths(
        root=workspace,
        raw_dir=workspace / "raw",
        wiki_dir=workspace / "wiki",
        sources_dir=workspace / "wiki" / "sources",
        concepts_dir=workspace / "wiki" / "concepts",
        queries_dir=workspace / "wiki" / "queries",
        outputs_dir=workspace / "outputs",
        slides_dir=workspace / "outputs" / "slides",
        figures_dir=workspace / "outputs" / "figures",
        config_path=workspace / ".openinstruct" / "kb.json",
        manifest_path=workspace / ".openinstruct" / "sources.json",
    )


def _slugify(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    return clean.strip("-") or "query"


def _raw_source_files(paths: KnowledgePaths) -> List[Path]:
    if not paths.raw_dir.exists():
        return []
    ignored_names = {".manifest.json", ".DS_Store"}
    files: List[Path] = []
    for path in sorted(paths.raw_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(paths.raw_dir)
        if rel.name in ignored_names:
            continue
        if rel.parts == ("README.md",):
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        files.append(path)
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".rst"}:
        return "text"
    if suffix in {".pdf"}:
        return "pdf"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if suffix in {".csv", ".tsv", ".json", ".jsonl", ".parquet"}:
        return "data"
    if suffix in {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp"}:
        return "code"
    mime, _ = mimetypes.guess_type(path.name)
    if mime:
        prefix = mime.split("/", 1)[0]
        if prefix in {"text", "image", "audio", "video"}:
            return prefix
    return "binary"


def _load_manifest(paths: KnowledgePaths) -> Dict[str, Any]:
    if not paths.manifest_path.exists():
        return {}
    return json.loads(paths.manifest_path.read_text(encoding="utf-8"))


def _save_manifest(paths: KnowledgePaths, payload: Dict[str, Any]) -> None:
    paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    paths.manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def init_knowledge_base(root: Path, name: str = "") -> Dict[str, str]:
    paths = knowledge_paths(root)
    for directory in (
        paths.raw_dir,
        paths.wiki_dir,
        paths.sources_dir,
        paths.concepts_dir,
        paths.queries_dir,
        paths.outputs_dir,
        paths.slides_dir,
        paths.figures_dir,
        paths.config_path.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().isoformat(timespec="seconds")
    config_payload = {
        "name": name or paths.root.name,
        "created_at": created_at,
        "layout": {
            "raw": "captured source material",
            "wiki": "compiled markdown knowledge base",
            "outputs": "derived outputs such as answers, slides and figures",
        },
        "conventions": {
            "wikilinks": True,
            "obsidian_friendly": True,
            "source_summary_dir": "wiki/sources",
            "concept_dir": "wiki/concepts",
            "query_dir": "wiki/queries",
        },
    }
    if not paths.config_path.exists():
        paths.config_path.write_text(json.dumps(config_payload, indent=2, ensure_ascii=True), encoding="utf-8")
    if not paths.manifest_path.exists():
        _save_manifest(
            paths,
            {
                "version": 1,
                "created_at": created_at,
                "updated_at": created_at,
                "root": str(paths.root),
                "raw_dir": "raw",
                "summary": {
                    "tracked": 0,
                    "added": 0,
                    "modified": 0,
                    "removed": 0,
                    "unchanged": 0,
                },
                "sources": {},
            },
        )

    index_path = paths.wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "\n".join(
                [
                    "# Knowledge Base",
                    "",
                    f"Workspace: `{paths.root}`",
                    f"Initialized: {created_at}",
                    "",
                    "## Sections",
                    "",
                    "- [[sources/index]]",
                    "- [[concepts/index]]",
                    "- [[queries/index]]",
                    "",
                    "## Notes",
                    "",
                    "- `raw/` stores imported source material.",
                    "- `wiki/` stores compiled markdown articles and indexes.",
                    "- `outputs/` stores generated answers, slides and figures.",
                ]
            ),
            encoding="utf-8",
        )

    for rel_path, title in (
        ("wiki/sources/index.md", "Source Summaries"),
        ("wiki/concepts/index.md", "Concepts"),
        ("wiki/queries/index.md", "Queries"),
        ("raw/README.md", "Raw Sources"),
    ):
        file_path = paths.root / rel_path
        if not file_path.exists():
            file_path.write_text(
                "\n".join(
                    [
                        f"# {title}",
                        "",
                        "This file is maintained by OpenInstruct.",
                    ]
                ),
                encoding="utf-8",
            )

    return {
        "root": str(paths.root),
        "config_path": str(paths.config_path),
        "manifest_path": str(paths.manifest_path),
        "raw_dir": str(paths.raw_dir),
        "wiki_dir": str(paths.wiki_dir),
        "outputs_dir": str(paths.outputs_dir),
    }


def ingest_sources(root: Path) -> Dict[str, Any]:
    paths = knowledge_paths(root)
    previous = _load_manifest(paths)
    previous_sources = dict(previous.get("sources") or {})
    sources: Dict[str, Dict[str, Any]] = {}
    added: List[str] = []
    modified: List[str] = []
    unchanged: List[str] = []
    removed: List[str] = []
    scanned_at = datetime.now().isoformat(timespec="seconds")

    for path in _raw_source_files(paths):
        rel = path.relative_to(paths.raw_dir).as_posix()
        record = {
            "path": rel,
            "sha256": _sha256(path),
            "size": path.stat().st_size,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "kind": _source_kind(path),
        }
        old = previous_sources.get(rel)
        if old is None:
            status = "added"
            added.append(rel)
        elif old.get("sha256") != record["sha256"]:
            status = "modified"
            modified.append(rel)
        else:
            status = "unchanged"
            unchanged.append(rel)
        record["status"] = status
        record["last_seen_at"] = scanned_at
        sources[rel] = record

    for rel in sorted(previous_sources):
        if rel not in sources:
            old = dict(previous_sources[rel])
            old["status"] = "removed"
            old["last_seen_at"] = scanned_at
            sources[rel] = old
            removed.append(rel)

    payload = {
        "version": 1,
        "created_at": previous.get("created_at") or scanned_at,
        "updated_at": scanned_at,
        "root": str(paths.root),
        "raw_dir": "raw",
        "summary": {
            "tracked": len(sources),
            "added": len(added),
            "modified": len(modified),
            "removed": len(removed),
            "unchanged": len(unchanged),
        },
        "changed_paths": {
            "added": added,
            "modified": modified,
            "removed": removed,
        },
        "sources": sources,
    }
    _save_manifest(paths, payload)
    return payload


def render_ingest_summary(root: Path, payload: Dict[str, Any] | None = None) -> str:
    paths = knowledge_paths(root)
    current = payload or _load_manifest(paths)
    if not current:
        return "(manifest not initialized)"
    summary = current.get("summary") or {}
    changed_paths = current.get("changed_paths") or {}
    lines = [
        f"manifest={paths.manifest_path}",
        f"tracked={summary.get('tracked', 0)}",
        f"added={summary.get('added', 0)}",
        f"modified={summary.get('modified', 0)}",
        f"removed={summary.get('removed', 0)}",
        f"unchanged={summary.get('unchanged', 0)}",
    ]
    for label in ("added", "modified", "removed"):
        items = list(changed_paths.get(label) or [])
        if items:
            lines.append(f"{label}: {', '.join(items[:8])}")
    return "\n".join(lines)


def knowledge_status(root: Path) -> Dict[str, object]:
    paths = knowledge_paths(root)
    manifest = _load_manifest(paths)
    summary = manifest.get("summary") or {}
    status = {
        "root": str(paths.root),
        "configured": paths.config_path.exists(),
        "manifest": paths.manifest_path.exists(),
        "raw_files": 0,
        "tracked_sources": int(summary.get("tracked", 0)),
        "raw_added": int(summary.get("added", 0)),
        "raw_modified": int(summary.get("modified", 0)),
        "raw_removed": int(summary.get("removed", 0)),
        "wiki_articles": 0,
        "query_articles": 0,
        "slides": 0,
        "figures": 0,
    }
    if paths.raw_dir.exists():
        status["raw_files"] = sum(1 for path in paths.raw_dir.rglob("*") if path.is_file())
    if paths.wiki_dir.exists():
        status["wiki_articles"] = sum(1 for path in paths.wiki_dir.rglob("*.md") if path.is_file())
    if paths.queries_dir.exists():
        status["query_articles"] = sum(1 for path in paths.queries_dir.rglob("*.md") if path.is_file())
    if paths.slides_dir.exists():
        status["slides"] = sum(1 for path in paths.slides_dir.rglob("*.md") if path.is_file())
    if paths.figures_dir.exists():
        status["figures"] = sum(1 for path in paths.figures_dir.rglob("*") if path.is_file())
    return status


def render_knowledge_status(root: Path) -> str:
    payload = knowledge_status(root)
    return "\n".join(
        [
            f"root={payload['root']}",
            f"configured={payload['configured']}",
            f"manifest={payload['manifest']}",
            f"raw_files={payload['raw_files']}",
            f"tracked_sources={payload['tracked_sources']}",
            f"raw_added={payload['raw_added']}",
            f"raw_modified={payload['raw_modified']}",
            f"raw_removed={payload['raw_removed']}",
            f"wiki_articles={payload['wiki_articles']}",
            f"query_articles={payload['query_articles']}",
            f"slides={payload['slides']}",
            f"figures={payload['figures']}",
        ]
    )


def build_compile_prompt(root: Path, scope: str = "") -> str:
    paths = knowledge_paths(root)
    scope_note = f"Focus scope: {scope}." if scope else "Process the full workspace incrementally."
    manifest = _load_manifest(paths)
    summary = manifest.get("summary") or {}
    changed_paths = manifest.get("changed_paths") or {}
    changed_lines: List[str] = []
    for label in ("added", "modified", "removed"):
        items = list(changed_paths.get(label) or [])
        if items:
            changed_lines.append(f"- {label}: {', '.join(items[:12])}")
    if not changed_lines:
        changed_lines.append("- no manifest delta recorded; inspect the workspace normally")
    return (
        "You are maintaining a markdown knowledge base for research work.\n"
        "Treat this workspace as a compiler pipeline from raw source material into a linked wiki.\n"
        "Goals:\n"
        "- inspect `raw/` for source material and `wiki/` for existing derived articles\n"
        "- update summaries under `wiki/sources/`\n"
        "- create or refresh concept pages under `wiki/concepts/`\n"
        "- maintain backlinks and index pages with Obsidian-friendly wikilinks\n"
        "- preserve previous useful work instead of rewriting the whole wiki blindly\n"
        "- when useful, create or update query artifacts under `wiki/queries/`\n"
        f"{scope_note}\n\n"
        f"Workspace root: {paths.root}\n"
        f"Raw sources: {paths.raw_dir}\n"
        f"Wiki: {paths.wiki_dir}\n"
        f"Outputs: {paths.outputs_dir}\n"
        f"Manifest: {paths.manifest_path}\n"
        "Incremental source manifest summary:\n"
        f"- tracked={summary.get('tracked', 0)} added={summary.get('added', 0)} modified={summary.get('modified', 0)} removed={summary.get('removed', 0)}\n"
        + "\n".join(changed_lines)
        + "\n"
        "Prefer editing markdown files directly in the workspace. End with a concise summary of what changed."
    )


def default_query_output_path(root: Path, question: str, output_format: str = "markdown") -> Path:
    paths = knowledge_paths(root)
    stem = _slugify(question)[:64]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_format == "marp":
        return paths.slides_dir / f"{timestamp}-{stem}.md"
    return paths.queries_dir / f"{timestamp}-{stem}.md"


def build_question_prompt(root: Path, question: str, output_path: Path, output_format: str = "markdown") -> str:
    paths = knowledge_paths(root)
    format_note = (
        "Write a Marp-compatible slide deck."
        if output_format == "marp"
        else "Write a markdown answer suitable for Obsidian."
    )
    return (
        "Answer the user's research question against the local knowledge base.\n"
        "Use the wiki as the primary source of truth, then inspect raw sources when needed.\n"
        "Prefer filing the result back into the knowledge base so future work compounds.\n"
        f"{format_note}\n"
        f"Write the final artifact to `{output_path.relative_to(paths.root)}`.\n"
        "If the wiki is missing important context, enrich it minimally while answering.\n"
        "Cite local files by path inside the markdown when useful.\n\n"
        f"Question:\n{question}"
    )


def build_lint_prompt(root: Path, fix: bool = False) -> str:
    action = (
        "Fix the issues you find directly in the markdown files, and summarize the repairs."
        if fix
        else "Do not edit files; produce a prioritized report of issues and next actions."
    )
    paths = knowledge_paths(root)
    return (
        "Audit this markdown knowledge base for integrity problems.\n"
        "Check for:\n"
        "- stale or missing backlinks\n"
        "- source summaries missing from raw material\n"
        "- concept pages that duplicate each other\n"
        "- broken wikilinks or missing target files\n"
        "- obvious factual inconsistencies between related notes\n"
        "- candidate new articles suggested by repeated mentions across files\n"
        f"{action}\n\n"
        f"Workspace root: {paths.root}\n"
        f"Wiki: {paths.wiki_dir}\n"
        f"Raw sources: {paths.raw_dir}"
    )
