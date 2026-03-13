"""
Neo4j catalog — GaaZoo graph schema.

Node labels
───────────
  (:Catalog)        – top-level catalog (e.g. "GaaZoo Catalog")
  (:Vendor)         – product vendor / source   (e.g. "Amazon", "Google Images")
  (:ProductType)    – broad type                (e.g. "Furniture")
  (:ProductSubType) – specific subtype          (e.g. "Sofa", "Chair")
  (:Image)          – one product image + metadata

Relationships
─────────────
  (:ProductType)   -[:IN_CATALOG]->     (:Catalog)
  (:ProductSubType)-[:TYPE_OF]->        (:ProductType)
  (:Image)         -[:CATEGORIZED_AS]-> (:ProductSubType)
  (:Image)         -[:FROM_VENDOR]->    (:Vendor)
"""

import json
import re
import shutil
from datetime import datetime, timezone
from typing import Optional

from neo4j import GraphDatabase

from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    DEFAULT_CATALOG_NAME, DEFAULT_VENDOR_NAME, DEFAULT_VENDOR_DOMAIN,
    DIR_2D, DIR_3D, DIR_DOLLHOUSE,
)

# ── Driver singleton ──────────────────────────────────────────────────
_driver = None

def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver

def _session():
    return _get_driver().session()

def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()

def _to_str(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


# ── Bootstrap: constraints ────────────────────────────────────────────

def init_db():
    """Create uniqueness constraints for all node labels (Catalog, Vendor, ProductType, ProductSubType, Image, Dollhouse)."""
    with _session() as s:
        constraints = [
            "CREATE CONSTRAINT catalog_id_unique   IF NOT EXISTS FOR (n:Catalog)        REQUIRE n.catalog_id  IS UNIQUE",
            "CREATE CONSTRAINT vendor_id_unique     IF NOT EXISTS FOR (n:Vendor)         REQUIRE n.vendor_id   IS UNIQUE",
            "CREATE CONSTRAINT type_id_unique       IF NOT EXISTS FOR (n:ProductType)    REQUIRE n.type_id     IS UNIQUE",
            "CREATE CONSTRAINT subtype_id_unique    IF NOT EXISTS FOR (n:ProductSubType) REQUIRE n.subtype_id  IS UNIQUE",
            "CREATE CONSTRAINT image_id_unique      IF NOT EXISTS FOR (n:Image)          REQUIRE n.image_id    IS UNIQUE",
            "CREATE CONSTRAINT dollhouse_id_unique  IF NOT EXISTS FOR (n:Dollhouse)      REQUIRE n.dollhouse_id IS UNIQUE",
        ]
        for c in constraints:
            s.run(c)


# ── Catalog node ──────────────────────────────────────────────────────

def ensure_catalog(name: str = DEFAULT_CATALOG_NAME) -> str:
    catalog_id = re.sub(r"[^\w]+", "-", name.lower()).strip("-")
    with _session() as s:
        s.run(
            """
            MERGE (c:Catalog {catalog_id: $catalog_id})
            ON CREATE SET c.name = $name, c.created_at = $now
            """,
            catalog_id=catalog_id, name=name, now=_now(),
        )
    return catalog_id


# ── Vendor node ───────────────────────────────────────────────────────

def ensure_vendor(
    name:          str = DEFAULT_VENDOR_NAME,
    source_domain: str = DEFAULT_VENDOR_DOMAIN,
    country:       str = "US",
) -> str:
    """Create or get Vendor node. Vendors are not linked to Catalog; Images link to Vendor."""
    vendor_id = re.sub(r"[^\w]+", "-", name.lower()).strip("-")
    with _session() as s:
        s.run(
            """
            MERGE (v:Vendor {vendor_id: $vendor_id})
            ON CREATE SET v.name          = $name,
                          v.source_domain = $source_domain,
                          v.country       = $country,
                          v.created_at    = $now
            """,
            {"vendor_id": vendor_id, "name": name,
             "source_domain": source_domain, "country": country,
             "now": _now()},
        )
    return vendor_id


# ── ProductType node (global, linked directly to Catalog) ─

def ensure_product_type(
    type_name:    str,
    catalog_name: str = DEFAULT_CATALOG_NAME,
    description:  str = "",
) -> str:
    """Create or get ProductType and link to Catalog (not to Vendor)."""
    type_id = re.sub(r"[^\w]+", "-", type_name.lower()).strip("-") or "general"
    catalog_id = ensure_catalog(catalog_name)
    with _session() as s:
        s.run(
            """
            MERGE (t:ProductType {type_id: $type_id})
            ON CREATE SET t.name = $name, t.description = $description
            WITH t
            MATCH (c:Catalog {catalog_id: $catalog_id})
            MERGE (t)-[:IN_CATALOG {added_at: $today}]->(c)
            """,
            {"type_id": type_id, "name": type_name, "description": description,
             "catalog_id": catalog_id, "today": _now()[:10]},
        )
    return type_id


# ── ProductSubType node (global: one per type+subtype) ─

def ensure_product_subtype(
    subtype_name: str,
    type_name:    str,
    catalog_name: str = DEFAULT_CATALOG_NAME,
    description:  str = "",
) -> str:
    type_id = ensure_product_type(type_name, catalog_name=catalog_name)
    subtype_slug = re.sub(r"[^\w]+", "-", subtype_name.lower()).strip("-") or "other"
    subtype_id = f"{type_id}--{subtype_slug}"
    with _session() as s:
        s.run(
            """
            MERGE (st:ProductSubType {subtype_id: $subtype_id})
            ON CREATE SET st.name = $name, st.description = $description
            WITH st
            MATCH (t:ProductType {type_id: $type_id})
            MERGE (st)-[:TYPE_OF {added_at: $today}]->(t)
            """,
            {"subtype_id": subtype_id, "name": subtype_name, "description": description,
             "type_id": type_id, "today": _now()[:10]},
        )
    return subtype_id


# ── Image node (core upsert) ──────────────────────────────────────────

def upsert_item(
    asin:              str,
    title:             Optional[str]  = None,
    vendor_name:       str            = DEFAULT_VENDOR_NAME,
    vendor_domain:     str            = DEFAULT_VENDOR_DOMAIN,
    product_type:      str            = "General",
    product_subtype:   str            = "Other",
    image_paths:       Optional[list] = None,
    image_path_used:   Optional[str]  = None,
    image_url:         Optional[str]  = None,
    source_url:        Optional[str]  = None,
    query:             Optional[str]  = None,
    style:             Optional[str]  = None,
    colour:            Optional[str]  = None,
    texture:           Optional[str]  = None,
    material:          Optional[str]  = None,
    width:             Optional[int]  = None,
    height:            Optional[int]  = None,
    product_dimensions: Optional[str]  = None,  # physical object dimensions e.g. "50 x 30 x 25 inches"
    mime_type:         Optional[str]  = None,
    image_base64:      Optional[str]  = None,
    glb_path:          Optional[str]  = None,
    conversion_status: Optional[str]  = None,
    meshy_task_id:     Optional[str]  = None,
    raw_metadata:      Optional[dict] = None,
) -> str:
    """
    Ensure Catalog → ProductType → ProductSubType chain and Vendor exist,
    MERGE :Image, attach [:CATEGORIZED_AS]->(subtype) and [:FROM_VENDOR]->(vendor).
    Returns the asin (image_id).
    """
    ensure_vendor(name=vendor_name, source_domain=vendor_domain)
    subtype_id = ensure_product_subtype(
        subtype_name=product_subtype,
        type_name=product_type,
        catalog_name=DEFAULT_CATALOG_NAME,
    )
    vendor_id = re.sub(r"[^\w]+", "-", vendor_name.lower()).strip("-")

    now = _now()

    with _session() as s:
        existing = s.run(
            "MATCH (i:Image {image_id: $asin}) RETURN i", asin=asin
        ).single()

        if existing:
            updates = {"updated_at": now}
            candidates = {
                "title":             _to_str(title),
                "vendor_name":       _to_str(vendor_name),
                "vendor_domain":     _to_str(vendor_domain),
                "product_type":      _to_str(product_type),
                "product_subtype":   _to_str(product_subtype),
                "image_paths":       json.dumps(image_paths) if image_paths is not None else None,
                "image_path_used":   _to_str(image_path_used),
                "image_url":         _to_str(image_url),
                "source_url":        _to_str(source_url),
                "query":             _to_str(query),
                "style":             _to_str(style),
                "colour":            _to_str(colour),
                "texture":           _to_str(texture),
                "material":          _to_str(material),
                "width":             width,
                "height":            height,
                "product_dimensions": _to_str(product_dimensions),
                "mime_type":         _to_str(mime_type),
                "image_base64":      _to_str(image_base64),
                "glb_path":          _to_str(glb_path),
                "conversion_status": _to_str(conversion_status),
                "meshy_task_id":     _to_str(meshy_task_id),
                "raw_metadata":      json.dumps(raw_metadata) if raw_metadata is not None else None,
            }
            for k, v in candidates.items():
                if v is not None:
                    updates[k] = v
            set_clause = ", ".join(f"i.{k} = ${k}" for k in updates)
            params = {"asin": asin, **updates}
            s.run(
                f"MATCH (i:Image {{image_id: $asin}}) SET {set_clause}",
                params,
            )
        else:
            params = {
                "asin": asin,
                "title":             _to_str(title) or "",
                "vendor_name":       _to_str(vendor_name) or "",
                "vendor_domain":     _to_str(vendor_domain) or "",
                "product_type":     _to_str(product_type) or "General",
                "product_subtype":   _to_str(product_subtype) or "Other",
                "image_paths":       json.dumps(image_paths) if image_paths else "[]",
                "image_path_used":   _to_str(image_path_used) or "",
                "image_url":         _to_str(image_url) or "",
                "source_url":        _to_str(source_url) or "",
                "query":             _to_str(query) or "",
                "style":             _to_str(style) or "",
                "colour":            _to_str(colour) or "",
                "texture":           _to_str(texture) or "",
                "material":          _to_str(material) or "",
                "width":             width,
                "height":            height,
                "product_dimensions": _to_str(product_dimensions) or "",
                "mime_type":         _to_str(mime_type) or "",
                "image_base64":      _to_str(image_base64) or "",
                "glb_path":          _to_str(glb_path) or "",
                "conversion_status": _to_str(conversion_status) or "pending",
                "meshy_task_id":     _to_str(meshy_task_id) or "",
                "raw_metadata":      json.dumps(raw_metadata) if raw_metadata else None,
                "now": now,
            }
            s.run(
                """
                CREATE (i:Image {
                    image_id:          $asin,
                    asin:              $asin,
                    title:             $title,
                    vendor_name:      $vendor_name,
                    vendor_domain:    $vendor_domain,
                    product_type:    $product_type,
                    product_subtype:  $product_subtype,
                    image_paths:       $image_paths,
                    image_path_used:   $image_path_used,
                    image_url:         $image_url,
                    source_url:        $source_url,
                    query:             $query,
                    style:             $style,
                    colour:            $colour,
                    texture:           $texture,
                    material:          $material,
                    width:             $width,
                    height:            $height,
                    product_dimensions: $product_dimensions,
                    mime_type:         $mime_type,
                    image_base64:      $image_base64,
                    glb_path:          $glb_path,
                    conversion_status: $conversion_status,
                    meshy_task_id:     $meshy_task_id,
                    raw_metadata:      $raw_metadata,
                    created_at:        $now,
                    updated_at:        $now
                })
                """,
                params,
            )

        s.run(
            """
            MATCH (i:Image {image_id: $asin}),
                  (st:ProductSubType {subtype_id: $subtype_id})
            MERGE (i)-[:CATEGORIZED_AS {tagged_at: $today}]->(st)
            """,
            {"asin": asin, "subtype_id": subtype_id, "today": now[:10]},
        )
        s.run(
            """
            MATCH (i:Image {image_id: $asin}), (v:Vendor {vendor_id: $vendor_id})
            MERGE (i)-[:FROM_VENDOR {since: $today}]->(v)
            """,
            {"asin": asin, "vendor_id": vendor_id, "today": now[:10]},
        )

    return asin


# ── Read helpers ──────────────────────────────────────────────────────

def _node_to_item(record) -> Optional[dict]:
    if record is None:
        return None
    node = record["i"]
    d = dict(node)
    for field in ("image_paths", "raw_metadata"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (TypeError, json.JSONDecodeError):
                d[field] = [] if field == "image_paths" else None
    return d


def get_item_by_asin(asin: str) -> Optional[dict]:
    with _session() as s:
        r = s.run("MATCH (i:Image {image_id: $asin}) RETURN i", asin=asin).single()
        return _node_to_item(r)


def list_items(
    conversion_status: Optional[str] = None,
    limit:  int = 100,
    offset: int = 0,
) -> list:
    """List Image nodes, optionally filtered by conversion_status."""
    with _session() as s:
        if conversion_status:
            rows = s.run(
                "MATCH (i:Image {conversion_status: $status}) "
                "RETURN i ORDER BY i.created_at DESC SKIP $skip LIMIT $limit",
                status=conversion_status, skip=offset, limit=limit,
            )
        else:
            rows = s.run(
                "MATCH (i:Image) "
                "RETURN i ORDER BY i.created_at DESC SKIP $skip LIMIT $limit",
                skip=offset, limit=limit,
            )
        return [_node_to_item(r) for r in rows]


def list_images_by_subtype(
    subtype_name: str,
    vendor_name:  Optional[str] = None,
    limit: int = 100,
) -> list:
    """Return all Image nodes for a given subtype, optionally filtered by vendor."""
    with _session() as s:
        if vendor_name:
            rows = s.run(
                """
                MATCH (img:Image)-[:FROM_VENDOR]->(v:Vendor {name: $vendor}),
                      (img)-[:CATEGORIZED_AS]->(:ProductSubType {name: $subtype})
                RETURN img AS i, v.name AS vendor_name
                ORDER BY img.created_at DESC LIMIT $limit
                """,
                subtype=subtype_name, vendor=vendor_name, limit=limit,
            )
        else:
            rows = s.run(
                """
                MATCH (img:Image)-[:CATEGORIZED_AS]->(:ProductSubType {name: $subtype}),
                      (img)-[:FROM_VENDOR]->(v:Vendor)
                RETURN img AS i, v.name AS vendor_name
                ORDER BY v.name, img.created_at DESC LIMIT $limit
                """,
                subtype=subtype_name, limit=limit,
            )
        results = []
        for r in rows:
            item = _node_to_item(r)
            if item:
                item["vendor_name"] = r.get("vendor_name", "")
                results.append(item)
        return results


def get_items_for_conversion(limit: int = 10) -> list:
    """Items that have a 2D image but no 3D yet (pending or failed)."""
    with _session() as s:
        rows = s.run(
            """
            MATCH (i:Image)
            WHERE i.image_path_used IS NOT NULL AND i.image_path_used <> ''
              AND (i.glb_path IS NULL OR i.glb_path = ''
                   OR i.conversion_status = 'failed')
            RETURN i ORDER BY i.created_at ASC LIMIT $limit
            """,
            limit=limit,
        )
        return [_node_to_item(r) for r in rows]


# ── Write helpers ─────────────────────────────────────────────────────

def update_conversion_result(
    asin:              str,
    glb_path:          Optional[str] = None,
    conversion_status: str           = "succeeded",
    meshy_task_id:     Optional[str] = None,
):
    """Update catalog item after 3D conversion."""
    now = _now()
    updates = {"conversion_status": conversion_status, "updated_at": now}
    if glb_path:      updates["glb_path"]      = glb_path
    if meshy_task_id: updates["meshy_task_id"] = meshy_task_id
    set_clause = ", ".join(f"i.{k} = ${k}" for k in updates)
    with _session() as s:
        s.run(
            f"MATCH (i:Image {{image_id: $asin}}) SET {set_clause}",
            asin=asin, **updates,
        )


def update_conversion_failed(asin: str, meshy_task_id: Optional[str] = None):
    update_conversion_result(
        asin, glb_path=None, conversion_status="failed",
        meshy_task_id=meshy_task_id,
    )


def delete_item(asin: str, delete_files: bool = True) -> bool:
    """Delete Image node and optionally remove local 2D/3D files."""
    item = get_item_by_asin(asin)
    if not item:
        return False
    with _session() as s:
        s.run("MATCH (i:Image {image_id: $asin}) DETACH DELETE i", asin=asin)
    if delete_files:
        safe = re.sub(r"[^\w\-.]", "_", asin)[:64]
        d2 = DIR_2D / safe
        if d2.is_dir():
            try: shutil.rmtree(d2)
            except OSError: pass
        glb = DIR_3D / f"{safe}.glb"
        if glb.is_file():
            try: glb.unlink()
            except OSError: pass
    return True


# ── Dollhouse node (Unity scan: name, scan_json, usdz file path) ───────

def upsert_dollhouse(
    name:       str,
    scan_json:  str,
    usdz_path:  str,
    dollhouse_id: Optional[str] = None,
) -> str:
    """
    Create or update a Dollhouse node with name, scan_json (string), and usdz_path.
    Returns dollhouse_id (generated uuid if not provided).
    """
    import uuid
    now = _now()
    did = dollhouse_id or ("dh_" + uuid.uuid4().hex[:12])
    with _session() as s:
        s.run(
            """
            MERGE (d:Dollhouse {dollhouse_id: $dollhouse_id})
            ON CREATE SET d.name = $name, d.scan_json = $scan_json, d.usdz_path = $usdz_path,
                          d.created_at = $now, d.updated_at = $now
            ON MATCH SET  d.name = $name, d.scan_json = $scan_json, d.usdz_path = $usdz_path,
                          d.updated_at = $now
            """,
            dollhouse_id=did,
            name=name,
            scan_json=scan_json,
            usdz_path=usdz_path,
            now=now,
        )
    return did


def _dollhouse_node_to_item(record) -> Optional[dict]:
    if record is None:
        return None
    node = record.get("d") or record.get("i")
    if node is None:
        return None
    d = dict(node)
    return d


def get_dollhouse(dollhouse_id: str) -> Optional[dict]:
    """Get a single Dollhouse node by dollhouse_id."""
    with _session() as s:
        r = s.run(
            "MATCH (d:Dollhouse {dollhouse_id: $id}) RETURN d",
            id=dollhouse_id,
        ).single()
        return _dollhouse_node_to_item({"d": r["d"]}) if r and r.get("d") else None


def list_dollhouses(limit: int = 100, offset: int = 0) -> list:
    """List all Dollhouse nodes, ordered by created_at desc."""
    with _session() as s:
        rows = s.run(
            """
            MATCH (d:Dollhouse)
            RETURN d
            ORDER BY d.created_at DESC
            SKIP $skip LIMIT $limit
            """,
            skip=offset,
            limit=limit,
        )
        return [_dollhouse_node_to_item({"d": r["d"]}) for r in rows if r.get("d")]


# ── Backward-compat alias ─────────────────────────────────────────────
def row_to_item(record) -> Optional[dict]:
    return _node_to_item(record)