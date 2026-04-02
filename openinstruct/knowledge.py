from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict


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
    )


def _slugify(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    return clean.strip("-") or "query"


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
        "raw_dir": str(paths.raw_dir),
        "wiki_dir": str(paths.wiki_dir),
        "outputs_dir": str(paths.outputs_dir),
    }


def knowledge_status(root: Path) -> Dict[str, object]:
    paths = knowledge_paths(root)
    status = {
        "root": str(paths.root),
        "configured": paths.config_path.exists(),
        "raw_files": 0,
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
            f"raw_files={payload['raw_files']}",
            f"wiki_articles={payload['wiki_articles']}",
            f"query_articles={payload['query_articles']}",
            f"slides={payload['slides']}",
            f"figures={payload['figures']}",
        ]
    )


def build_compile_prompt(root: Path, scope: str = "") -> str:
    paths = knowledge_paths(root)
    scope_note = f"Focus scope: {scope}." if scope else "Process the full workspace incrementally."
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
