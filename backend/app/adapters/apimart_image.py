from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

GENERATE_URL = "https://api.apimart.ai/v1/images/generations"
TASK_URL = "https://api.apimart.ai/v1/tasks/{task_id}?language=en"


async def generate_image2(
    *,
    api_key: str,
    prompt: str,
    output_path: Path,
    size: str = "16:9",
    resolution: str = "2k",
    timeout_sec: int = 180,
) -> dict:
    """Generate one gpt-image-2 image via APIMart and download it locally."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            GENERATE_URL,
            headers=headers,
            json={
                "model": "gpt-image-2",
                "prompt": prompt,
                "n": 1,
                "size": size,
                "resolution": resolution,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        task_id = payload.get("task_id")
        if not task_id and isinstance(payload.get("data"), list) and payload["data"]:
            first = payload["data"][0]
            if isinstance(first, dict):
                task_id = first.get("task_id")
        if not task_id:
            raise RuntimeError(f"APIMart did not return task_id: {resp.text}")

    return await download_task_image(
        api_key=api_key,
        task_id=task_id,
        output_path=output_path,
        timeout_sec=timeout_sec,
    )


def _normalize_task_payload(payload: dict) -> dict:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return payload


def _walk_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith("http") and any(ext in value.lower() for ext in (".png", ".jpg", ".jpeg", ".webp")):
            urls.append(value)
        return urls
    if isinstance(value, list):
        for item in value:
            urls.extend(_walk_urls(item))
        return urls
    if isinstance(value, dict):
        for key in ("url", "image_url", "source_url", "download_url"):
            v = value.get(key)
            if isinstance(v, str) and v.startswith("http"):
                urls.append(v)
            elif isinstance(v, list):
                urls.extend(_walk_urls(v))
        for v in value.values():
            urls.extend(_walk_urls(v))
    return urls


def _extract_image_url(data: dict) -> str | None:
    images = (
        data.get("images")
        or data.get("output")
        or data.get("result")
        or data.get("result_urls")
        or data.get("files")
        or []
    )
    if isinstance(images, dict):
        images = [images]
    if isinstance(images, str):
        return images
    if images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url")
        return str(first)
    for key in ("url", "image_url"):
        if data.get(key):
            return str(data[key])
    urls = _walk_urls(data)
    if urls:
        return urls[0]
    return None


async def download_task_image(
    *,
    api_key: str,
    task_id: str,
    output_path: Path,
    timeout_sec: int = 180,
) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        image_url = None
        last_payload: dict = {}
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(3)
            task = await client.get(TASK_URL.format(task_id=task_id), headers=headers)
            task.raise_for_status()
            last_payload = _normalize_task_payload(task.json())
            status = str(last_payload.get("status") or "").lower()
            print(f"task {task_id} status={status or 'unknown'}", flush=True)
            if status in {"failed", "error"}:
                raise RuntimeError(f"APIMart task failed: {last_payload}")
            image_url = _extract_image_url(last_payload)
            if image_url:
                break
        if not image_url:
            raise TimeoutError(f"APIMart task timed out: {task_id}; last={last_payload}")

        image = await client.get(image_url)
        image.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image.content)
        return {"task_id": task_id, "source_url": image_url, "file": str(output_path)}


async def fetch_task_payload(*, api_key: str, task_id: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        task = await client.get(TASK_URL.format(task_id=task_id), headers=headers)
        task.raise_for_status()
        return task.json()
