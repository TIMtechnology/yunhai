#!/usr/bin/env python3
"""导出 prediction_access_log + outcome 供离线分析。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.cloudsea_store import export_prediction_feedback, init_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="导出预测访问反馈")
    parser.add_argument("--spot-id")
    parser.add_argument("--viewpoint-id")
    parser.add_argument("--month", help="YYYY-MM")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("-o", "--output", help="输出文件路径")
    args = parser.parse_args()

    init_store()
    payload = export_prediction_feedback(
        spot_id=args.spot_id,
        viewpoint_id=args.viewpoint_id,
        month=args.month,
        export_format=args.format,
    )

    if args.format == "csv":
        body = payload["body"]
        out = args.output or "prediction_feedback.csv"
        Path(out).write_text(body, encoding="utf-8")
        print(f"Wrote {out}")
    else:
        out = args.output or "prediction_feedback.json"
        Path(out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {out} ({payload.get('count', 0)} records)")


if __name__ == "__main__":
    main()
