"""Scale a GLB/OBJ 3D model to match user-specified real-world dimensions."""
import numpy as np
from pathlib import Path

UNIT_TO_METERS = {
    "m":  1.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": 0.0254,
    "ft": 0.3048,
}


def scale_model(
    input_path: str,
    output_path: str,
    width: float = None,
    height: float = None,
    depth: float = None,
    unit: str = "cm",
) -> dict:
    """
    Load a GLB/OBJ model, rescale it to the specified real-world dimensions,
    and export to output_path (GLB).

    Axis convention (GLTF/GLB standard):
        width  → X axis
        height → Y axis
        depth  → Z axis

    When only some dimensions are provided the missing axes are scaled by the
    average of the provided scale factors, preserving rough proportions while
    still respecting the given constraints.

    Returns a dict with original/scaled sizes and individual scale factors.
    """
    try:
        import trimesh
    except ImportError:
        raise ImportError(
            "trimesh is required for 3D scaling. "
            "Install it with: pip install trimesh"
        )

    factor = UNIT_TO_METERS.get((unit or "cm").lower().strip(), 0.01)
    target_w = float(width)  * factor if width  is not None else None
    target_h = float(height) * factor if height is not None else None
    target_d = float(depth)  * factor if depth  is not None else None

    mesh = trimesh.load(str(input_path))
    bounds = mesh.bounds
    if bounds is None:
        raise ValueError("Model has no geometry (empty bounding box).")

    orig = bounds[1] - bounds[0]
    orig_w, orig_h, orig_d = float(orig[0]), float(orig[1]), float(orig[2])

    provided = {}
    if target_w is not None and orig_w > 1e-9:
        provided["x"] = target_w / orig_w
    if target_h is not None and orig_h > 1e-9:
        provided["y"] = target_h / orig_h
    if target_d is not None and orig_d > 1e-9:
        provided["z"] = target_d / orig_d

    if not provided:
        raise ValueError(
            "No valid dimensions provided, or the model has zero extent on the "
            "requested axes."
        )

    num_provided = len(provided)
    if num_provided == 1:
        # Scale: one factor for all axes — maintains proportions (no skew)
        (_, scale_factor) = provided.popitem()
        sx = sy = sz = scale_factor
        mode = "scale"
    else:
        # Resize: force dimensions — each axis independent; missing axes use average
        avg = sum(provided.values()) / len(provided)
        sx = provided.get("x", avg)
        sy = provided.get("y", avg)
        sz = provided.get("z", avg)
        mode = "resize"

    scale_matrix = np.diag([sx, sy, sz, 1.0])
    mesh.apply_transform(scale_matrix)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    mesh.export(str(output_path))

    new_bounds = mesh.bounds
    new_size   = new_bounds[1] - new_bounds[0]

    def _to_unit(meters: float) -> float:
        return round(meters / factor, 2)

    return {
        "mode": mode,
        "original": {
            "w": _to_unit(orig_w),
            "h": _to_unit(orig_h),
            "d": _to_unit(orig_d),
        },
        "scaled": {
            "w": _to_unit(float(new_size[0])),
            "h": _to_unit(float(new_size[1])),
            "d": _to_unit(float(new_size[2])),
        },
        "scale_factors": {
            "x": round(sx, 5),
            "y": round(sy, 5),
            "z": round(sz, 5),
        },
        "unit": unit,
    }
