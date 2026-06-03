#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters.apimart_image import generate_image2  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate share OG backgrounds via APIMart gpt-image-2")
    parser.add_argument("--manifest", default=str(ROOT / "data/share-assets/manifest.json"))
    parser.add_argument("--only", help="template id")
    parser.add_argument("--task-id", help="resume an existing APIMart task and download it for --only")
    parser.add_argument("--debug-task", action="store_true", help="print a redacted task payload and exit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("APIMART_API_KEY", "")
    if not args.dry_run and not api_key:
        api_key = getpass.getpass("APIMART_API_KEY（输入不会回显，不会写入文件）: ").strip()
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    for item in manifest.get("templates", []):
        if args.only and item["id"] != args.only:
            continue
        out = base / item["file"]
        print(f"{item['id']}: {out}")
        print(item["prompt"])
        if args.dry_run:
            continue
        if not api_key:
            raise SystemExit("APIMART_API_KEY is required; set env or enter it interactively")
        if args.task_id:
            from app.adapters.apimart_image import download_task_image, fetch_task_payload

            if args.debug_task:
                payload = await fetch_task_payload(api_key=api_key, task_id=args.task_id)
                print(json.dumps(payload, ensure_ascii=False, indent=2)[:6000])
                return

            meta = await download_task_image(api_key=api_key, task_id=args.task_id, output_path=out)
        else:
            meta = await generate_image2(api_key=api_key, prompt=item["prompt"], output_path=out)
        (base / f"{item['id']}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
