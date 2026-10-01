"""Validation test for Open Knowledge Format (OKF) v0.2 bundle conformance."""

import re
from pathlib import Path

import yaml

KNOWLEDGE_ROOT = Path(__file__).parent.parent / "knowledge"


def test_okf_root_index():
    index_path = KNOWLEDGE_ROOT / "index.md"
    assert index_path.exists(), "knowledge/index.md must exist"

    content = index_path.read_text(encoding="utf-8")
    assert content.startswith("---"), "Root index.md must start with YAML frontmatter"

    # Parse frontmatter
    parts = content.split("---", 2)
    assert len(parts) >= 3, "Frontmatter not cleanly closed with '---'"
    meta = yaml.safe_load(parts[1])
    assert meta.get("okf_version") == "0.2", "Root index must declare okf_version: '0.2'"
    assert "title" in meta
    assert "description" in meta


def test_okf_concepts_frontmatter():
    concept_files = [
        f for f in KNOWLEDGE_ROOT.rglob("*.md")
        if f.name not in ("index.md", "log.md")
    ]
    assert len(concept_files) >= 3, "Expected at least 3 OKF concept files"

    for f in concept_files:
        rel_path = f.relative_to(KNOWLEDGE_ROOT)
        content = f.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{rel_path}: missing opening '---' frontmatter"

        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{rel_path}: frontmatter not properly closed with '---'"

        meta = yaml.safe_load(parts[1])
        assert isinstance(meta, dict), f"{rel_path}: frontmatter must be a YAML dictionary"

        # OKF required / normative fields
        assert "type" in meta and meta["type"], f"{rel_path}: missing or empty 'type'"
        assert "id" in meta and meta["id"], f"{rel_path}: missing or empty 'id'"
        assert "title" in meta and meta["title"], f"{rel_path}: missing or empty 'title'"
        assert "status" in meta and meta["status"] in ("active", "stable", "draft", "deprecated"), f"{rel_path}: invalid status"
        assert "trust" in meta, f"{rel_path}: missing 'trust'"
        assert "generated" in meta, f"{rel_path}: missing 'generated'"
        assert "sources" in meta and isinstance(meta["sources"], list), f"{rel_path}: missing or invalid 'sources'"


def test_okf_markdown_links():
    """Verify that internal links in knowledge bundle point to existing files."""
    for md_file in KNOWLEDGE_ROOT.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        links = re.findall(r"\[.*?\]\((.*?\.md)\)", content)
        for link in links:
            target = (md_file.parent / link).resolve()
            assert target.exists(), f"{md_file.name} links to non-existent file: {link}"
