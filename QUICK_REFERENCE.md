# Blender Studio — Quick Reference

One-page cheat sheet for daily CAD modeling, inspection, and slicing inside OrcaSlicer.

---

## 1. Top Bar & Server Modes

| Control | Behavior |
| :--- | :--- |
| **Start Headless Server** | Launches Blender in `--background` mode running `server_bootstrap.py` on `:9999` (fast, zero viewport overhead). |
| **Launch Blender GUI** | Opens full Blender window with the persistent `:9999` socket timer attached (survives opening other `.blend` files). |
| **Refresh** | Re-scans your 3D designs folder and syncs live objects, dimensions, and active `.blend` state from Blender. |
| **Save .blend** | Saves the active scene in Blender (`Untitled_Studio.blend` in your projects folder if unsaved). |
| **Stop Server** | Sends `__stop_server__ = True` over `:9999` and shuts down the Blender socket server cleanly. |
| **Settings** | Configures Blender binary, 3D designs directory, template `.blend`, remote host/port, AI endpoints, and render resolution. |

---

## 2. Left Panel Tabs

| Tab | What It Shows & Key Actions |
| :--- | :--- |
| **Scene** | Splits live scene meshes into **Printable Meshes** (with `X × Y × Z mm` & face count + **Export STL** / **Send to Plate**) and **Hardware Mockups** (collapsed by default; click **Show** to inspect). |
| **Projects** | Lists `.blend` files in your designs directory (newest first). Click **Open in Server** (auto-starts headless Blender if offline), **Open GUI**, or **+ New Project (.blend)** (`1 BU = 1 mm` metric setup). |
| **STL / 3MF** | Lists all existing `.stl`, `.3mf`, `.obj`, `.step`, and `.amf` files in your designs directory with one-click **Send to Plate**. |

---

## 3. Diagnostic Camera Presets

| Preset | Projection & Purpose |
| :--- | :--- |
| **Hero** | 45 mm perspective 3/4 view aimed at printable mesh bounding-box center — overall aesthetic & assembly check. |
| **Top** | Orthographic straight down (`+Z -> -Z`) — XY hole spacing, wall thickness, and pocket alignment check. |
| **Front** | Orthographic horizontal (`-Y -> +Y`) — port cutout height, lid gap, and Z-layer alignment check. |
| **Isometric** | Orthographic 3/4 mechanical view — dimensional proportion check without perspective distortion. |
| **Exploded** | Temporarily fans printable parts outward around the assembly center, renders Hero angle, and restores exact original locations afterward. |
| **Scene Cam** | Uses the active `scene.camera` already stored in your `.blend` file. |

---

## 4. Built-in CAD Macros

| Button | Action |
| :--- | :--- |
| **`Rig`** | Adds or updates `KeyLight`, `FillLight`, `RimLight`, and `RenderCam` aimed at the scene bounding-box center. |
| **`+Magnet`** | Adds `Mockup_Mag_N` (`Ø6.25 × 2.20 mm`) into `Hardware_Mockups` for press-fit `6×2 mm` neodymium disc pockets. |
| **`+M3`** | Adds `M_Screw_M3_N` (`Ø3.4 mm` clearance shaft + `5.65 mm` AF DIN 934 hex nut pocket) into `Hardware_Mockups`. |
| **`+SG90`** | Adds `M_SG90_N` (`23.0 × 12.5 × 22.0 mm` servo body mockup) into `Hardware_Mockups`. |
| **`Audit`** | Runs `bmesh` check on every printable mesh: reports `X × Y × Z mm`, `min_Z`, non-manifold edges, loose verts, inverted normals, and unapplied non-`1.0` scale. |

---

## 5. Naming & 3D-Printing Conventions

- **Scale:** `1 Blender Unit = 1.0 mm` (`unit_settings.system = 'METRIC'`, `scale_length = 0.001`). Always export with `use_scene_unit=False, global_scale=1.0`.
- **Mockup Classification:** Meshes are automatically excluded from printable exports & STL batching if:
  - Name starts with `M_`, `Mockup_`, `Vis_`, `Plug_`, `Ground_Shadow`, `Drawing_Paper`, or `Floor`
  - **Or** the object lives in a `Hardware_Mockups` / `Cable_Plug_Mockups` / `Col_Mockups` / `Col_Plugs` collection (and not a printable/case/inlay collection).

### Standard FDM Print Clearances (`0.4 mm` nozzle)
| Interface | Recommended Offset | Example Nominal -> Modeled |
| :--- | :--- | :--- |
| **Press-Fit Magnet** | `+0.25 mm` diameter, `+0.20 mm` depth | `6×2 mm` disc -> `Ø6.25 × 2.20 mm` pocket |
| **Slide / Dovetail / Lid Lip** | `+0.20 mm` per mating side | `51.6 mm` cavity -> `51.2 mm` lid lip |
| **Clamp / Pen Bore** | `+0.30 mm` diameter | `11.5 mm` barrel -> `11.8 mm` bore |
| **M3 Clearance Hole** | `+0.40 mm` diameter | `M3` -> `Ø3.4 mm` hole (`r = 1.7 mm`) |
| **M3 Captive Hex Nut** | `+0.15 mm` across flats | `5.5 mm` DIN 934 -> `5.65 mm` AF (`r = 3.25 mm` 6-sided) |

---

## 6. Essential `bpy` Snippets & Shortcuts

- **`Ctrl + Enter`** inside the `bpy` editor: Execute script in Blender immediately.

```python
# Convex chamfer / fillet in Blender 5.x bmesh (profile=0.5 is critical!)
bmesh.ops.bevel(bm, geom=edges, offset=2.0, segments=4, profile=0.5, affect="EDGES", clamp_overlap=True)
```

```python
# Clean EXACT boolean difference & delete cutter
mod = target.modifiers.new(name="Cut", type="BOOLEAN")
mod.operation = "DIFFERENCE"
mod.solver = "EXACT"
mod.object = cutter
bpy.context.view_layer.objects.active = target
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.data.objects.remove(cutter, do_unlink=True)
```

---

## 7. Multi-Modal Context Attachments (Pictures & Documents)

You can feed visual references and technical specs directly into the **AI CAD Assistant**:

| Input Method | Supported Formats & Behavior |
| :--- | :--- |
| **`+ Image / Doc` button** | Opens multi-file picker for pictures and documents; attached chips appear beneath the prompt box with thumbnail/badge and `×` remove buttons. |
| **Drag & Drop** | Drag one or more files directly onto the **AI CAD Assistant** prompt area. |
| **Clipboard Paste (`Ctrl+V`)** | Paste screenshots or copied images directly into the prompt textarea. |
| **`+Viewport` checkbox** | Includes the active EEVEE diagnostic render shown in the viewport alongside your prompt and uploaded files. |

- **Pictures (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`):** Sent as multi-modal image inputs (auto-downscaled above `1536 px` so local GPU vision models stay fast). If Ollama is set to a text-only coder model like `qwen2.5-coder:7b`, Blender Studio automatically switches to an installed local vision model (such as `lukey03/qwen3.5-9b-abliterated-vision:latest`) when images are attached.
- **Documents (`.md`, `.txt`, `.json`, `.csv`, `.py`, `.scad`, `.yaml`, `.yml`, `.xml`, `.svg`, `.html`, `.pdf`, `.log`, `.ini`, `.toml`):** Parsed client-side (plus pure-stdlib `FlateDecode` PDF text extraction on the backend, up to `24k chars/doc`, `64k chars` total) and injected cleanly as reference context so **both** text coder models and multimodal models can read specs, pinouts, BOMs, and existing CAD code.

---

## 8. TCP `:9999` Wire Protocol (External Tools & Agents)

Every packet uses a **4-byte big-endian unsigned integer length header** followed by UTF-8 payload:

- **Request:** `[4-byte length][UTF-8 Python code string]`
- **Response:** `[4-byte length][UTF-8 JSON]`

```json
{
  "status": "success",
  "stdout": "...",
  "stderr": "",
  "file_data": null
}
```
Set `__file_data__ = <json-serializable>` inside your script to return structured data, or `__stop_server__ = True` to shut down the server.
