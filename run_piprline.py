#!/usr/bin/env python3
"""
CLI to run the end-to-end pipeline locally.

  # Fetch from Amazon
  python run_pipeline.py fetch "wireless mouse" --max 5

  # Fetch from Google Images via SerpAPI
  python run_pipeline.py fetch-serp "modern sofa" --num 10 --country us

  # Convert pending items to 3D
  python run_pipeline.py convert --limit 3

  # List catalog
  python run_pipeline.py catalog [--status pending] [--limit 20]
"""
import argparse
import sys


def cmd_fetch(args):
    from pipeline_amazon import run_amazon_pipeline
    result = run_amazon_pipeline(
        query=args.query,
        country=args.country,
        max_products=args.max,
    )
    print(f"Fetched and stored {len(result)} items from Amazon:")
    for r in result:
        print(f"  [{r.get('product_type','?')} / {r.get('product_subtype','?')}]"
              f"  {r['asin']}  —  {r.get('title','')[:50]}")
    return 0


def cmd_fetch_serp(args):
    from pipeline_serp import run_serp_pipeline
    result = run_serp_pipeline(
        query=args.query,
        num=args.num,
        country=args.country,
        vendor_name=args.vendor,
    )
    print(f"Fetched and stored {len(result)} images from Google Images (SerpAPI):")
    for r in result:
        print(f"  [{r.get('product_type','?')} / {r.get('product_subtype','?')}]"
              f"  {r['asin']}  —  {r.get('title','')[:50]}")
        if r.get("source_domain"):
            print(f"    source: {r['source_domain']}")
    return 0


def cmd_convert(args):
    from pipeline_3d import run_3d_pipeline
    result = run_3d_pipeline(limit=args.limit)
    print(f"Conversion results: {len(result)}")
    for r in result:
        line = f"  {r['asin']}: {r['status']}"
        if r.get("glb_path"):
            line += f"  ->  {r['glb_path']}"
        print(line)
    return 0


def cmd_catalog(args):
    from catalog_db import init_db, list_items
    init_db()
    items = list_items(conversion_status=args.status, limit=args.limit)
    print(f"Catalog: {len(items)} items")
    for it in items:
        print(f"  {it['asin']} | {it.get('title','')[:40]}"
              f" | status={it.get('conversion_status')}"
              f" | glb={it.get('glb_path') or '-'}")
    return 0


def main():
    p   = argparse.ArgumentParser(description="GaaZoo catalog pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    # ── fetch (Amazon) ────────────────────────────────────────────────
    f = sub.add_parser("fetch", help="Fetch products from Amazon, store in catalog")
    f.add_argument("query", help="Search query e.g. 'wireless mouse'")
    f.add_argument("--country", default="US",  help="Amazon country code (default: US)")
    f.add_argument("--max",     type=int, default=5, help="Max products (default: 5)")
    f.set_defaults(run=cmd_fetch)

    # ── fetch-serp (Google Images) ────────────────────────────────────
    fs = sub.add_parser("fetch-serp", help="Fetch images from Google Images via SerpAPI")
    fs.add_argument("query",  help="Search query e.g. 'modern sofa plain background'")
    fs.add_argument("--num",     type=int, default=10,    help="Number of images (default: 10)")
    fs.add_argument("--country", default="us",            help="Google gl country param (default: us)")
    fs.add_argument("--vendor",  default="Google Images", help="Vendor label in graph (default: Google Images)")
    fs.set_defaults(run=cmd_fetch_serp)

    # ── convert ───────────────────────────────────────────────────────
    c = sub.add_parser("convert", help="Convert pending catalog items to 3D via Meshy")
    c.add_argument("--limit", type=int, default=3, help="Max items to convert (default: 3)")
    c.set_defaults(run=cmd_convert)

    # ── catalog ───────────────────────────────────────────────────────
    cat = sub.add_parser("catalog", help="List catalog items")
    cat.add_argument("--status", choices=["pending", "succeeded", "failed"], default=None)
    cat.add_argument("--limit",  type=int, default=50)
    cat.set_defaults(run=cmd_catalog)

    args = p.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
