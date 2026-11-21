#!/usr/bin/env python3
"""Sync BOOTSTRAP_SOURCE from blender_studio.py to standalone/server_bootstrap.py."""
from pathlib import Path

studio_path = Path("blender_studio.py")
bootstrap_path = Path("standalone/server_bootstrap.py")

studio_text = studio_path.read_text(encoding="utf-8")

prefix1 = 'BOOTSTRAP_SOURCE = r"""'
prefix2 = "BOOTSTRAP_SOURCE = r'''"

if prefix1 in studio_text:
    start = studio_text.find(prefix1) + len(prefix1)
    end_marker = '"""\n\n\n# --------------------------------------------------------------------------- #'
    end = studio_text.find(end_marker, start)
elif prefix2 in studio_text:
    start = studio_text.find(prefix2) + len(prefix2)
    end_marker = "'''\n\n\n# --------------------------------------------------------------------------- #"
    end = studio_text.find(end_marker, start)
else:
    raise ValueError("Neither prefix found in blender_studio.py")

if end == -1:
    raise ValueError("End marker not found in blender_studio.py")

bootstrap_source = studio_text[start:end]
bootstrap_path.write_text(bootstrap_source, encoding="utf-8")
print(f"Exported BOOTSTRAP_SOURCE ({len(bootstrap_source)} chars) to {bootstrap_path}")
