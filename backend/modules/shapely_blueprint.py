"""
Shapely Layout Demo — Blueprint image generator.

Renders the uploaded layout JSON as a 2D blueprint: room boundary and objects
with dimensions (in mm). Uses Pillow; coordinates are scaled to fit the image.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from modules.shapely_rule_engine import ROOM_KEY, layout_json_to_geometries

# Image size and margin (pixels)
BLUEPRINT_WIDTH = 920
BLUEPRINT_HEIGHT = 620
MARGIN = 40

# Colors (RGB)
ROOM_OUTLINE = (80, 80, 80)
ROOM_FILL = (248, 248, 250)
OBJECT_FILLS = [
    (230, 245, 255),  # light blue
    (255, 245, 230),  # light orange
    (230, 255, 230),  # light green
    (255, 230, 245),  # light pink
    (245, 230, 255),  # light purple
]
OBJECT_OUTLINE = (60, 60, 60)
TEXT_COLOR = (40, 40, 40)
DIMENSION_COLOR = (100, 100, 100)


def _bbox_from_geometries(geometries: dict) -> tuple[float, float, float, float]:
    """Return (minx, miny, maxx, maxy) for all polygons."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for name, poly in geometries.items():
        if poly is None or not hasattr(poly, "bounds"):
            continue
        b = poly.bounds
        minx = min(minx, b[0])
        miny = min(miny, b[1])
        maxx = max(maxx, b[2])
        maxy = max(maxy, b[3])
    if minx == float("inf"):
        return 0, 0, 5000, 4000
    return minx, miny, maxx, maxy


def _scale_point(
    x: float, y: float,
    minx: float, miny: float, maxx: float, maxy: float,
    width: int, height: int, margin: int,
) -> tuple[int, int]:
    """Map world coords (mm) to image pixel coords (y flipped so origin top-left)."""
    if maxx <= minx:
        maxx = minx + 1
    if maxy <= miny:
        maxy = miny + 1
    px = margin + (x - minx) / (maxx - minx) * (width - 2 * margin)
    # Image y: top = 0, so flip world y (world y increases upward or downward depending on input)
    py = margin + (maxy - y) / (maxy - miny) * (height - 2 * margin)
    return int(round(px)), int(round(py))


def _polygon_to_pixel_coords(poly, minx, miny, maxx, maxy, width, height, margin) -> list[tuple[int, int]]:
    """Convert polygon exterior to list of pixel (x,y) for drawing."""
    if not hasattr(poly, "exterior") or poly.exterior is None:
        return []
    coords = list(poly.exterior.coords)[:-1]  # drop closing point
    return [
        _scale_point(float(c[0]), float(c[1]), minx, miny, maxx, maxy, width, height, margin)
        for c in coords
    ]


def _dimensions_mm(poly) -> tuple[float, float]:
    """Return (width_mm, height_mm) of bounding box."""
    if poly is None or not hasattr(poly, "bounds"):
        return 0, 0
    b = poly.bounds
    return round(b[2] - b[0], 0), round(b[3] - b[1], 0)


def layout_to_blueprint_png(layout: dict[str, Any]) -> bytes:
    """
    Generate a PNG blueprint image from layout JSON (room + objects with coords).
    Returns PNG bytes. Assumes coordinates are in mm.
    """
    geometries = layout_json_to_geometries(layout)
    if not geometries:
        raise ValueError("No valid room or objects found in layout.")

    minx, miny, maxx, maxy = _bbox_from_geometries(geometries)
    width = BLUEPRINT_WIDTH
    height = BLUEPRINT_HEIGHT
    margin = MARGIN

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Draw room first (if present)
    room_poly = geometries.get(ROOM_KEY)
    if room_poly is not None:
        room_pts = _polygon_to_pixel_coords(room_poly, minx, miny, maxx, maxy, width, height, margin)
        if len(room_pts) >= 3:
            draw.polygon(room_pts, fill=ROOM_FILL, outline=ROOM_OUTLINE, width=3)
            w_mm, h_mm = _dimensions_mm(room_poly)
            cx = sum(p[0] for p in room_pts) / len(room_pts)
            cy = sum(p[1] for p in room_pts) / len(room_pts)
            draw.text((int(cx) - 50, int(cy) - 8), f"Room: {int(w_mm)} × {int(h_mm)} mm", fill=TEXT_COLOR)

    # Draw objects (excluding room)
    color_index = 0
    for name, poly in geometries.items():
        if name == ROOM_KEY or poly is None:
            continue
        pts = _polygon_to_pixel_coords(poly, minx, miny, maxx, maxy, width, height, margin)
        if len(pts) < 3:
            continue
        fill = OBJECT_FILLS[color_index % len(OBJECT_FILLS)]
        color_index += 1
        draw.polygon(pts, fill=fill, outline=OBJECT_OUTLINE, width=2)
        w_mm, h_mm = _dimensions_mm(poly)
        # Label: name and dimensions at centroid-ish (first vertex slightly up)
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        label = f"{name.replace('_', ' ')}: {int(w_mm)}×{int(h_mm)} mm"
        draw.text((int(cx) - 40, int(cy) - 8), label, fill=TEXT_COLOR)

    # Export PNG
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
