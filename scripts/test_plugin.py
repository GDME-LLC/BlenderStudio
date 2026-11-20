#!/usr/bin/env python3
"""Smoke tests for Blender Studio plugin structure and metadata."""
import ast
from pathlib import Path
import py_compile
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC_PY = ROOT / "blender_studio.py"


def test_syntax():
    py_compile.compile(str(SRC_PY), doraise=True)
    print("✓ Syntax and bytecode compilation OK")


def test_pep723_metadata():
    content = SRC_PY.read_text(encoding="utf-8")
    assert "# /// script" in content, "Missing PEP 723 header '# /// script'"
    assert "# [tool.orcaslicer.plugin]" in content, "Missing '[tool.orcaslicer.plugin]'"

    m_name = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
    assert m_name and m_name.group(1) == "Blender Studio", f"Invalid plugin name: {m_name}"

    m_ver = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
    assert m_ver and m_ver.group(1) == "1.2.5", f"Invalid version in header: {m_ver}"

    m_const = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    assert m_const and m_const.group(1) == m_ver.group(1), "Version mismatch between header and PLUGIN_VERSION"
    print(f"✓ PEP 723 metadata verified (v{m_ver.group(1)})")


def test_ast_classes():
    content = SRC_PY.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(SRC_PY))
    classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}

    assert "BlenderStudioPage" in classes, "Missing BlenderStudioPage class"
    assert "BlenderStudioPlugin" in classes, "Missing BlenderStudioPlugin class"
    print("✓ Capability classes found (BlenderStudioPage, BlenderStudioPlugin)")


def test_settings_structure():
    content = SRC_PY.read_text(encoding="utf-8")
    assert "DEFAULT_SETTINGS" in content, "Missing DEFAULT_SETTINGS"
    assert "blender_bin" in content
    assert "server_port" in content
    assert "projects_dir" in content
    print("✓ Settings schema and defaults verified")


def test_dist_assets():
    dist = ROOT / "dist"
    assert dist.is_dir(), "dist/ directory missing"
    assert (dist / "blender_studio_any.py").is_file(), "blender_studio_any.py missing"
    assert (dist / "icon.png").is_file(), "icon.png missing"
    assert (dist / "icon.svg").is_file(), "icon.svg missing"
    assert (dist / "hub_hero_banner.png").is_file(), "hub_hero_banner.png missing"
    assert (dist / "workflow_pipeline.png").is_file(), "workflow_pipeline.png missing"
    assert (dist / "diagnostic_views_showcase.png").is_file(), "diagnostic_views_showcase.png missing"
    print("✓ Distribution assets verified in dist/ (including showcase media)")


def test_changelog():
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md is missing"
    content = changelog.read_text(encoding="utf-8")
    assert "## [1.2.5]" in content, "CHANGELOG.md missing current version entry ## [1.2.5]"
    latest = ROOT / "dist" / "latest_changelog.md"
    assert latest.is_file(), "dist/latest_changelog.md is missing"
    print("✓ CHANGELOG.md and dist/latest_changelog.md verified")


def main():
    print(f"Running Blender Studio tests against {SRC_PY.name}...")
    test_syntax()
    test_pep723_metadata()
    test_ast_classes()
    test_settings_structure()
    test_dist_assets()
    test_changelog()
    print("All tests passed successfully!")


if __name__ == "__main__":
    main()
