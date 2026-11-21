## [1.2.7] - 2026-11-21
### Interactive Model Cutting & Multi-Plate Part Separation (✂ Cut Macro & Modal)

#### Added & Enhanced
- Interactive Cut & Separate Modal (`✂ Cut`): Effortlessly split complex 3D models into watertight pieces or separate loose disconnected shells for independent build plates directly from the mesh sidebar.
- Watertight Manifold Bisect & Capping: Advanced planar bisecting with automated hole filling, face normal recalculation, and guaranteed 0 non-manifold edges.
- Automatic Z-Floor Grounding & Part Spacing: Automatically aligns cut parts flat against the build bed (Z=0) with configurable spacing (default 25mm) for instant collision-free multi-plate slicing.
- Built-in `✂ Cut` Macro (`cut_split`): Instant macro button in the bpy script bar with fallback execution directly in Blender Python editor.
- Procedural CAD Helpers: Added `cad.cut_parts(...)` and `cad.separate_loose(...)` to the embedded `cad` library and exposed `bpy.cad`.

#### Fixed & Security
- Ensured full coordinate transform application (`location=True, rotation=True, scale=True`) prior to bisecting to guarantee 100% accurate planar positioning.
