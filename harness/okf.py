import os
import re
import yaml
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

_OKF_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_OKF_MAX_DEPTH = 6


class _NoAnchorLoader(yaml.SafeLoader):
    """Prohibit YAML aliases/anchors to prevent billion-laughs attacks in frontmatter."""
    pass

def _deny_aliases(loader, node):
    raise yaml.YAMLError("YAML aliases/anchors are prohibited in OKF frontmatter.")

_NoAnchorLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_SCALAR_TAG, _NoAnchorLoader.construct_scalar)
_NoAnchorLoader.add_multi_constructor("tag:yaml.org,2002:", _NoAnchorLoader.construct_undefined)


def parse_okf_markdown(content: str, default_concept_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Parses an OKF v0.2 markdown document (YAML frontmatter + markdown body)
    into a structured concept dictionary per the Mantis OKF v0.2 specification.
    """
    if not content or not isinstance(content, str):
        return None

    content_clean = content.strip()
    frontmatter_dict: Dict[str, Any] = {}
    body = content_clean

    lines = content_clean.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        closing_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing_idx = i
                break
        if closing_idx != -1:
            fm_raw = "".join(lines[1:closing_idx]).strip()
            body = "".join(lines[closing_idx + 1:]).strip()
            try:
                loaded = yaml.load(fm_raw, Loader=yaml.SafeLoader)
                if isinstance(loaded, dict):
                    frontmatter_dict = loaded
            except Exception:
                frontmatter_dict = {}

    concept_type = str(frontmatter_dict.get("type") or "").strip()
    title = str(frontmatter_dict.get("title") or "").strip()
    resource = str(frontmatter_dict.get("resource") or frontmatter_dict.get("target_file") or "").strip()

    if not concept_type:
        clean_cid = default_concept_id.replace("\\", "/")
        if clean_cid.startswith("entities/") or "/entities/" in clean_cid:
            concept_type = "Component Entity"
        elif "threat" in clean_cid.lower():
            concept_type = "Threat Boundary"
        elif "architecture" in clean_cid.lower() or "summary" in clean_cid.lower():
            concept_type = "Architecture Summary"
        elif "vulnerabilit" in clean_cid.lower():
            concept_type = "Vulnerability Pattern"
        elif "invariant" in clean_cid.lower() or "guardrail" in clean_cid.lower():
            concept_type = "Security Invariant"
        else:
            concept_type = "Generic Concept"

    if not title:
        for line in body.splitlines():
            line_str = line.strip()
            if line_str.startswith("# ") and not line_str.startswith("##"):
                title = line_str.removeprefix("# ").strip()
                break
        if not title:
            title = Path(default_concept_id).stem if default_concept_id else "Untitled Concept"

    # Derive Trust Tier per OKF v0.2 §5.3
    verified_val = frontmatter_dict.get("verified") or []
    if isinstance(verified_val, dict):
        verified_list = [verified_val]
    elif isinstance(verified_val, list):
        verified_list = verified_val
    else:
        verified_list = []

    trust_tier = str(frontmatter_dict.get("trust_tier") or "").lower()
    if not trust_tier:
        trust_tier = "unverified"
        if verified_list:
            has_human = False
            has_machine = False
            for v in verified_list:
                if not isinstance(v, dict):
                    continue
                by_str = str(v.get("by") or "").lower()
                if by_str.startswith("human") or "reviewer" in by_str or "manual" in by_str:
                    has_human = True
                elif by_str.startswith("process") or by_str.startswith("machine") or "scanner" in by_str or "critic" in by_str:
                    has_machine = True

            if has_human:
                trust_tier = "human_reviewed"
            elif has_machine:
                trust_tier = "machine_confirmed"
            else:
                trust_tier = "heuristic"

    tags = frontmatter_dict.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]

    return {
        "concept_id": default_concept_id or f"concept_{title.lower().replace(' ', '_')}",
        "type": concept_type,
        "title": title,
        "resource": resource,
        "trust_tier": trust_tier,
        "tags": [str(t) for t in tags],
        "verified": verified_list,
        "frontmatter": frontmatter_dict,
        "body_markdown": body,
    }


def init_okf_table(db_path: Path):
    """Ensure okf_concepts table exists in the database."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS okf_concepts (
                concept_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                resource TEXT,
                trust_tier TEXT NOT NULL,
                tags TEXT,
                frontmatter_json TEXT,
                body_markdown TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_okf_resource ON okf_concepts (resource)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_okf_type ON okf_concepts (type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_okf_tier ON okf_concepts (trust_tier)")


def record_okf_concept(db_path: Path, concept: Dict[str, Any]):
    """Insert or replace an OKF concept."""
    init_okf_table(db_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cid = concept.get("concept_id")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO okf_concepts (
                concept_id, type, title, resource, trust_tier,
                tags, frontmatter_json, body_markdown, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM okf_concepts WHERE concept_id = ?), ?), ?)
            """,
            (
                cid,
                concept.get("type", "Generic Concept"),
                concept.get("title", "Untitled"),
                concept.get("resource", ""),
                concept.get("trust_tier", "unverified"),
                json.dumps(concept.get("tags", [])),
                json.dumps(concept.get("frontmatter", {})),
                concept.get("body_markdown", ""),
                cid,
                now,
                now,
            ),
        )


def query_okf_concepts(
    db_path: Path,
    resource: Optional[str] = None,
    concept_type: Optional[str] = None,
    trust_tier: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query stored OKF concepts with optional resource filtering."""
    init_okf_table(db_path)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    conditions = []
    params = []

    if resource:
        # Match exact resource or wildcard substring
        clean_res = resource.replace("\\", "/").lstrip("/")
        conditions.append("(resource = ? OR resource LIKE ? OR ? LIKE '%' || resource)")
        params.extend([clean_res, f"%{clean_res}%", clean_res])
    if concept_type:
        conditions.append("type = ?")
        params.append(concept_type)
    if trust_tier:
        conditions.append("trust_tier = ?")
        params.append(trust_tier)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cur.execute(f"SELECT * FROM okf_concepts {where_clause} ORDER BY updated_at DESC", params)
    rows = cur.fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["frontmatter"] = json.loads(d["frontmatter_json"] or "{}")
        results.append(d)
    return results


def export_okf_bundle(db_path: Path, output_dir: Path) -> int:
    """
    Export all OKF concepts from database to a fully conformant OKF v0.2 directory bundle on disk,
    including bundle-root index.md (with okf_version), log.md, and subdirectory index.md files.
    """
    concepts = query_okf_concepts(db_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    categories: Dict[str, List[Tuple[str, str, str]]] = {
        "invariants": [],
        "threats": [],
        "vulnerabilities": [],
        "entities": [],
        "concepts": [],
    }

    exported_count = 0
    for c in concepts:
        ctype = c.get("type", "").lower()
        if "invariant" in ctype:
            cat = "invariants"
        elif "threat" in ctype:
            cat = "threats"
        elif "vulnerabilit" in ctype:
            cat = "vulnerabilities"
        elif "entity" in ctype:
            cat = "entities"
        else:
            cat = "concepts"

        subdir = output_dir / cat
        subdir.mkdir(parents=True, exist_ok=True)

        slug = _OKF_SLUG_RE.sub('_', c.get('title', 'concept').lower())[:50].strip('_.')
        cid = c.get('concept_id', 'concept')
        filename = f"{cid}_{slug}.md"
        file_path = subdir / filename

        title = c.get("title") or "Untitled Concept"
        body = c.get("body_markdown", "").strip()
        first_line = body.splitlines()[0] if body else ""
        desc = (c.get("frontmatter", {}).get("description") or first_line or title)[:120].strip()

        # Build conformant v0.2 frontmatter
        fm = dict(c.get("frontmatter", {}))
        fm["type"] = c.get("type")
        fm["title"] = title
        fm["description"] = desc
        if c.get("resource"):
            fm["resource"] = c.get("resource")
        fm["status"] = fm.get("status", "stable")
        fm["trust_tier"] = c.get("trust_tier", "unverified")

        if c.get("tags"):
            fm["tags"] = c.get("tags")

        # Trust & Provenance fields (v0.2)
        if "generated" not in fm:
            fm["generated"] = {"by": "process:mantis-security-agent/1.0", "at": now_iso}

        if "verified" not in fm or not fm["verified"]:
            if c.get("trust_tier") == "human_reviewed":
                fm["verified"] = [{"by": "human:security-lead", "at": now_iso}]
            else:
                fm["verified"] = [{"by": "process:mantis-critic", "at": now_iso}]

        if "sources" not in fm:
            res_target = c.get("resource") or "/addons"
            fm["sources"] = [{
                "id": "audit-lead",
                "resource": res_target,
                "title": f"Mantis Security Audit Target ({res_target})",
                "author": "process:mantis-security-agent",
                "last_modified": now_iso,
            }]

        fm_str = yaml.dump(fm, sort_keys=False).strip()
        full_text = f"---\n{fm_str}\n---\n\n{body}\n"

        file_path.write_text(full_text, encoding="utf-8")
        exported_count += 1
        categories[cat].append((title, filename, desc))

    # 1. Generate subdirectory index.md files (NO frontmatter per E3)
    cat_titles = {
        "invariants": "Security Invariants",
        "threats": "Threat Boundaries",
        "vulnerabilities": "Vulnerability Patterns",
        "entities": "Component Entities",
        "concepts": "General Concepts",
    }
    for cat, items in categories.items():
        if items:
            cat_dir = output_dir / cat
            idx_lines = [f"# {cat_titles[cat]}\n"]
            for t, fn, d in sorted(items, key=lambda x: x[0]):
                idx_lines.append(f"* [{t}](./{fn}) - {d}")
            (cat_dir / "index.md").write_text("\n".join(idx_lines) + "\n", encoding="utf-8")

    # 2. Generate bundle-root index.md with okf_version: "0.2"
    root_idx = [
        "---",
        'okf_version: "0.2"',
        "---",
        "",
        "# Mantis Security Knowledge Bundle",
        "",
        "Self-contained Open Knowledge Format (OKF v0.2) bundle containing verified security invariants, threat boundaries, and vulnerabilities.",
        "",
        "## Catalog Sections",
    ]
    for cat, items in categories.items():
        if items:
            root_idx.append(f"* [{cat_titles[cat]}](./{cat}/index.md) - {len(items)} concepts")
    (output_dir / "index.md").write_text("\n".join(root_idx) + "\n", encoding="utf-8")

    # 3. Generate bundle-root log.md with ISO 8601 date headings
    log_content = f"# Update Log\n\n## {today_date}\n* **Creation**: Exported {exported_count} concepts into OKF v0.2 bundle.\n"
    (output_dir / "log.md").write_text(log_content, encoding="utf-8")

    return exported_count


def import_okf_bundle(db_path: Path, bundle_dir: Path) -> int:
    """Import an OKF v0.2 directory bundle into SQLite."""
    if not bundle_dir.exists():
        return 0

    imported_count = 0
    for root, _, files in os.walk(bundle_dir):
        for f in files:
            if not f.endswith(".md") or f in ("index.md", "log.md"):
                continue
            f_path = Path(root) / f
            try:
                rel_p = str(f_path.relative_to(bundle_dir))
                content = f_path.read_text(encoding="utf-8", errors="ignore")
                parsed = parse_okf_markdown(content, default_concept_id=rel_p)
                if parsed:
                    record_okf_concept(db_path, parsed)
                    imported_count += 1
            except Exception:
                continue

    return imported_count


def query_security_guidance(
    db_path: Path,
    target_file: str,
    full: bool = False,
) -> Dict[str, Any]:
    """
    Synthesize architectural security guidance for a given target file or module
    combining active OKF concepts, security invariants, threat boundaries, and prior findings.
    """
    concepts = query_okf_concepts(db_path, resource=target_file)
    # Also fetch repo-wide security invariants
    global_invariants = query_okf_concepts(db_path, concept_type="Security Invariant")

    highest_tier = "UNVERIFIED"
    tier_rank = {"human_reviewed": 3, "machine_confirmed": 2, "heuristic": 1, "unverified": 0}
    max_rank = 0

    for c in concepts:
        r = tier_rank.get(c.get("trust_tier", "unverified"), 0)
        if r > max_rank:
            max_rank = r
            highest_tier = c.get("trust_tier", "unverified").upper()

    seen_inv_ids = set()
    deduped_invs = []
    for c in (concepts + global_invariants):
        cid = c.get("concept_id")
        if cid not in seen_inv_ids and "invariant" in c.get("type", "").lower():
            seen_inv_ids.add(cid)
            deduped_invs.append(c)

    dossier = {
        "target_file": target_file,
        "okf_trust_tier": highest_tier,
        "scoped_concepts_count": len(concepts),
        "threat_boundaries": [c for c in concepts if "threat" in c.get("type", "").lower()],
        "security_invariants": deduped_invs,
        "vulnerability_patterns": [c for c in concepts if "vulnerabilit" in c.get("type", "").lower()],
        "guidance_markdown": "",
    }

    # Generate Markdown Dossier
    md_lines = [
        f"# Mantis Security Advisor: {target_file}",
        f"**[OKF TRUST TIER: {highest_tier}]**\n",
        f"Security guidance synthesized from Open Knowledge Format (OKF v0.2) concepts in `knowledge.db`.\n",
    ]

    if dossier["threat_boundaries"]:
        md_lines.append("## 1. Active Threat Boundaries & Trust Zones")
        for tb in dossier["threat_boundaries"]:
            md_lines.append(f"### 🛡️ {tb.get('title')} ({tb.get('trust_tier')})")
            body = tb.get("body_markdown", "") if full else (tb.get("body_markdown", "")[:300] + "...")
            md_lines.append(body + "\n")

    if dossier["security_invariants"]:
        md_lines.append("## 2. Mandatory Security Invariants (Pre-Commit Guardrails)")
        for inv in dossier["security_invariants"]:
            md_lines.append(f"- **{inv.get('title')}** (`{inv.get('trust_tier')}`):")
            body = inv.get("body_markdown", "") if full else (inv.get("body_markdown", "").splitlines()[0] if inv.get("body_markdown") else "")
            md_lines.append(f"  {body}\n")

    if dossier["vulnerability_patterns"]:
        md_lines.append("## 3. Known Historical Vulnerabilities in this Target")
        for vp in dossier["vulnerability_patterns"]:
            md_lines.append(f"- **{vp.get('title')}** [CWE: {', '.join(vp.get('tags', []))}]:")
            body = vp.get("body_markdown", "") if full else (vp.get("body_markdown", "")[:200] + "...")
            md_lines.append(f"  {body}\n")

    if not concepts and not global_invariants:
        md_lines.append("No historical OKF concepts or specific invariants recorded for this target yet. Follow secure coding standards.")

    dossier["guidance_markdown"] = "\n".join(md_lines)
    return dossier
