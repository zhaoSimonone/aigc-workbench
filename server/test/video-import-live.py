#!/usr/bin/env python3
"""Verify a prepared real-video manifest using an explicit acceptance account."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills/video-inspiration-import/scripts"))
import import_video as importer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    cookie = args.workdir / "primary-session.json"
    client = importer.load_uploader().Client(args.api, cookie)
    user = client.require("GET", "/api/auth/me")["user"]
    receipt = json.loads((args.workdir / "receipt.json").read_text())
    assert user["id"] == receipt["accounts"][0]["id"]
    assert user["email"].startswith("acceptance-") and user["email"].endswith("@example.invalid")
    manifest = importer.read_json(args.manifest)
    checks, results = [], []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
        print("PASS " + name, flush=True)

    for item in manifest["items"]:
        result = importer.upload_one(item, client)
        asset_id = result["asset"]["id"]
        repeated = importer.upload_one(item, client)
        check(item["platform"] + " repeated import reuses ID",
              repeated["status"] == "existing" and repeated["asset"]["id"] == asset_id)
        detail = client.get_asset(asset_id)
        check(item["platform"] + " media and source readback",
              detail["sha256"] == item["sha256"] and detail["sourceUrl"] == item["sourceUrl"]
              and detail["mediaMetadata"]["width"] == item["media"]["width"])
        media = args.workdir / (item["platform"] + "-roundtrip.mp4")
        client.download(f"/api/assets/{asset_id}/stream", media)
        check(item["platform"] + " downloaded COS bytes match", importer.digest(media) == item["sha256"])
        verified = importer.verify_media(media)
        check(item["platform"] + " uploaded media fully decodes with non-silent audio",
              verified["passed"] and verified["audio"]
              and all(a["maxVolumeDb"] != "-inf" for a in verified["audio"]))
        check(item["platform"] + " generated previews cleaned",
              not (Path(item["stateFile"]).parent / ".identify/preview.jpg").exists())
        results.append(result)
    item, result = manifest["items"][0], results[0]
    new_name = "验收-" + item["name"]
    assert len(new_name) <= 32
    organize = REPO / "skills/aigc-asset-organize/scripts/organize_assets.py"
    command = [sys.executable, str(organize), "--api", args.api, "--cookie-file", str(cookie),
               "apply", "--asset-id", result["asset"]["id"], "--set-name", new_name,
               "--reason", "临时验收账号验证服务端改名后的清单刷新"]
    preview = subprocess.run(command, capture_output=True, text=True)
    check("organize CLI preview leaves server unchanged", preview.returncode == 0
          and client.get_asset(result["asset"]["id"])["name"] == item["name"])
    applied = subprocess.run(command + ["--confirm"], capture_output=True, text=True)
    check("organize CLI applies confirmed change", applied.returncode == 0
          and client.get_asset(result["asset"]["id"])["name"] == new_name)
    args.cookie_file = cookie
    with contextlib.redirect_stdout(io.StringIO()):
        check_status = importer.refresh(args)
    updated = importer.read_json(args.manifest)["items"][0]
    check("refresh reconciles manifest and state", check_status == 0 and updated["name"] == new_name
          and importer.read_json(item["stateFile"])["asset"]["name"] == new_name
          and updated["sha256"] == item["sha256"])
    client.require("PATCH", f"/api/assets/{result['asset']['id']}/organize",
                   json.dumps({"name": item["name"], "before": {"name": new_name}}).encode(),
                   {"Content-Type": "application/json", "X-AIGC-Metadata-Policy": "curated-v1"})
    with contextlib.redirect_stdout(io.StringIO()):
        assert importer.refresh(args) == 0
    importer.write_json(args.workdir / "import-receipt.json",
                        {"passed": True, "checks": checks, "results": results})
    print(f"PASS all {len(checks)} video workflow checks", flush=True)


if __name__ == "__main__":
    main()
