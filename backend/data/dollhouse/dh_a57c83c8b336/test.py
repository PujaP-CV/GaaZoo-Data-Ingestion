import zipfile
import os
from pxr import Usd, UsdGeom
import trimesh
import numpy as np

# USDZ is a zip file - extract it
with zipfile.ZipFile("Dollhouse.usdz", 'r') as zip_ref:
    zip_ref.extractall("extracted_usdz")

# Find the USD file inside
usd_file = None
for f in os.listdir("extracted_usdz"):
    if f.endswith(('.usdc', '.usda', '.usd')):
        usd_file = os.path.join("extracted_usdz", f)
        break

# Open with USD
stage = Usd.Stage.Open(usd_file)

# Extract meshes
for prim in stage.Traverse():
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        points = np.array(mesh.GetPointsAttr().Get())
        face_indices = np.array(mesh.GetFaceVertexIndicesAttr().Get())
        face_counts = np.array(mesh.GetFaceVertexCountsAttr().Get())
        
        # Convert to trimesh (assuming triangles)
        if all(c == 3 for c in face_counts):
            faces = face_indices.reshape(-1, 3)
            tri_mesh = trimesh.Trimesh(vertices=points, faces=faces)
            tri_mesh.show()