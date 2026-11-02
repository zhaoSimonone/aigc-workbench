#!/usr/bin/env python3
"""Explicit live acceptance run against PostgreSQL/COS using temporary accounts.

Requires --api and --workdir. Creates synthetic media and private session files.
Leaves a receipt for scoped cleanup and browser acceptance; never uses an existing
user's session. Run only against an environment authorized for write testing.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "skills/aigc-asset-upload/scripts"))
from upload_assets import Client, MultipartBody


def save_private(path, data):
    with path.open("w", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        json.dump(data, handle, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    os.chmod(args.workdir, 0o700)
    receipt = {"api": args.api, "accounts": [], "assets": [], "checks": [],
               "passed": False, "startedAt": time.time()}
    receipt_path = args.workdir / "receipt.json"

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        receipt["checks"].append(name)
        save_private(receipt_path, receipt)
        print("PASS " + name, flush=True)

    def json_request(client, method, path, data=None, status=200, strict=True):
        headers = {"Content-Type": "application/json"}
        if strict:
            headers["X-AIGC-Metadata-Policy"] = "curated-v1"
        return client.require(method, path,
                              json.dumps(data).encode() if data is not None else None,
                              headers, expected=status)

    def register(label):
        email = f"acceptance-{uuid.uuid4().hex}@example.invalid"
        password = secrets.token_urlsafe(24)
        client = Client(args.api, args.workdir / f"{label}-session.json")
        user = json_request(client, "POST", "/api/auth/register",
                            {"name": "发布验收-" + label, "email": email, "password": password},
                            status=201)["user"]
        receipt["accounts"].append({"id": user["id"], "email": email})
        save_private(receipt_path, receipt)
        save_private(args.workdir / f"{label}-login.json", {"email": email, "password": password})
        return client, password

    def multipart(client, route, media, fields, status=201, strict=True):
        body = MultipartBody(media, "video/mp4", fields)
        try:
            headers = {"Content-Type": body.content_type, "Content-Length": str(body.length)}
            if strict:
                headers["X-AIGC-Metadata-Policy"] = "curated-v1"
            return client.require("POST", route, body, headers, expected=status)
        finally:
            body.file.close()

    def remember(asset):
        receipt["assets"].append(asset["id"])
        save_private(receipt_path, receipt)
        return asset

    try:
        anonymous = Client(args.api, args.workdir / "anonymous-session.json")
        check("health", anonymous.require("GET", "/api/health")["ok"])
        check("anonymous API is denied", anonymous.request("GET", "/api/assets")[0] == 401)
        a, password = register("primary")
        b, _ = register("other")
        check("two isolated test accounts", a.require("GET", "/api/auth/me")["user"]["id"] !=
              b.require("GET", "/api/auth/me")["user"]["id"])
        check("metadata policy", a.metadata_policy()["policy"] == "curated-v1")
        video = args.workdir / "fixture.mp4"
        second = args.workdir / "replacement.mp4"
        png = args.workdir / "fixture.png"
        gif = args.workdir / "fixture.gif"
        for target, size in [(video, "160x90"), (second, "320x180")]:
            subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", "1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-movflags", "+faststart", str(target)], check=True)
        for target in (png, gif):
            subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(video), "-frames:v", "1", str(target)], check=True)
        candidate = {"work": "验收作品", "name": "验收角色", "confidence": 1.0,
                     "confirmed": True, "evidence": "合成测试夹具，用于验证字段保存"}
        metadata = {"name": "验收-色块与音轨", "tags": ["验收", "色块", "音轨"],
                    "source": "发布验收", "sourceUrl": "https://example.invalid/acceptance",
                    "folder": "灵感收集", "characterName": "验收角色",
                    "characterCandidates": [candidate]}
        asset = remember(a.upload(video, metadata))
        asset_id = asset["id"]
        detail = a.get_asset(asset_id)
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        check("multipart Chinese metadata and SHA-256",
              detail["name"] == metadata["name"] and set(detail["tags"]) == set(metadata["tags"])
              and detail["sha256"] == digest and detail["sizeBytes"] == video.stat().st_size)
        check("real ffprobe metadata", detail["mediaMetadata"]["width"] == 160
              and detail["mediaMetadata"]["videoCodec"] == "h264"
              and detail["mediaMetadata"]["audioCodecs"] == ["aac"]
              and 1 < detail["durationSeconds"] < 2)
        check("character evidence readback", detail["characterCandidates"] == [candidate])
        check("exact dedup", a.find_existing([digest])[digest]["id"] == asset_id)
        check("account isolation detail", b.request("GET", f"/api/assets/{asset_id}")[0] == 404)
        check("account isolation hashes", not b.find_existing([digest]))
        check("account isolation lists", b.list_assets() == [])
        check("detail JSON 404", a.request("GET", f"/api/assets/{uuid.uuid4()}")[0] == 404)
        check("search filter", [x["id"] for x in a.list_assets(q="色块", folder="灵感收集")] == [asset_id])
        check("confirmed character filter", [x["id"] for x in a.list_assets(
            characterWork="验收作品", characterName="验收角色", confirmedCharacter="true")] == [asset_id])
        check("unmatched character is excluded", not a.list_assets(characterName="其他",
                                                                  confirmedCharacter="true"))
        check("duration filter", not a.list_assets(duration_min=100))
        for route in ("stream", "download", "download-url"):
            downloaded = args.workdir / f"download-{route}.mp4"
            url = f"/api/assets/{asset_id}/{route}"
            if route == "download-url":
                url = a.require("GET", url)["url"]
            a.download(url, downloaded)
            check("COS bytes via " + route, hashlib.sha256(downloaded.read_bytes()).hexdigest() == digest)
        a.download(detail["thumbUrl"], args.workdir / "thumb.jpg")
        check("COS thumbnail", (args.workdir / "thumb.jpg").stat().st_size > 0)
        changed = json_request(a, "PATCH", f"/api/assets/{asset_id}/organize",
                               {"name": "验收-已整理", "before": {"name": detail["name"],
                                "tags": list(reversed(detail["tags"])), "characterCandidates": [candidate]}})
        check("organize snapshot and tag order", changed["asset"]["name"] == "验收-已整理")
        json_request(a, "PATCH", f"/api/assets/{asset_id}/organize",
                     {"used": True, "before": {"name": detail["name"]}}, status=409)
        check("stale snapshot does not write", a.get_asset(asset_id)["used"] is False)
        lint = json_request(a, "PATCH", f"/api/assets/{asset_id}/organize",
                            {"name": "bad.name", "tags": ["one"]}, status=400)
        check("field-level lint", {x["field"] for x in lint["fields"]} == {"name", "tags"})
        for media, folder in [(png, "服装库"), (gif, "GIF 素材")]:
            image = remember(a.upload(media, {**metadata, "folder": folder}))
            check("image media " + media.suffix, image["contentType"] == ("image/png" if media == png else "image/gif"))
            a.download(image["directUrl"], args.workdir / ("roundtrip" + media.suffix))
            check("image bytes " + media.suffix, (args.workdir / ("roundtrip" + media.suffix)).read_bytes() == media.read_bytes())
        page = a.require("GET", "/api/assets?limit=1&offset=1")
        check("real SQL pagination", page["total"] == 3 and len(page["assets"]) == 1 and page["hasMore"])
        check("legacy unpaginated list", len(a.require("GET", "/api/assets")["assets"]) == 3)
        batch = json_request(a, "PATCH", "/api/assets", {"items": [
            {"id": asset_id, "favorite": True}, {"id": receipt["assets"][1], "tags": [None, "b", "c"]}]})
        check("batch partial success with fields", batch["results"][0]["ok"]
              and batch["results"][1]["status"] == 400 and batch["results"][1]["fields"][0]["field"] == "tags")
        json_request(a, "PATCH", f"/api/assets/{asset_id}/organize",
                     {"parentAssetIds": [receipt["assets"][1]]})
        check("parent relationships", a.get_asset(asset_id)["parentAssetIds"] == [receipt["assets"][1]])
        json_request(b, "PATCH", f"/api/assets/{asset_id}/organize", {"favorite": True}, status=404)
        check("cross account update rejected", True)
        legacy = remember(multipart(a, "/api/assets", second,
                                   {"name": "legacy.draft", "tags": '["draft"]'}, strict=False)["asset"])
        check("legacy draft upload remains usable", legacy["name"] == "legacy.draft" and legacy["tags"] == ["draft"])
        json_request(a, "PATCH", f"/api/assets/{legacy['id']}", {"used": True}, strict=False)
        check("legacy PATCH", a.get_asset(legacy["id"])["used"])
        similar = a.list_assets(similarTo=legacy["id"])
        check("real SQL similar candidates", any(x["id"] == asset_id for x in similar))
        replaced = multipart(a, f"/api/assets/{asset_id}/file", second, {}, status=200)["asset"]
        check("replacement refreshes metadata", replaced["mediaMetadata"]["width"] == 320
              and replaced["sha256"] == hashlib.sha256(second.read_bytes()).hexdigest()
              and replaced["name"] == "验收-已整理")
        ticket = json_request(a, "POST", "/api/assets/upload-ticket",
                              {"filename": "ticket.mp4", "contentType": "video/mp4"})
        with a._open("PUT", ticket["url"], video.read_bytes(),
                     {"Content-Type": "video/mp4", "Cache-Control": "private, max-age=86400, immutable"}) as response:
            check("direct COS PUT", response.status == 200)
        finalized = remember(json_request(a, "POST", "/api/assets/finalize-upload",
                                         {**metadata, "objectKey": ticket["objectKey"]}, status=201)["asset"])
        check("ticket finalize readback", a.get_asset(finalized["id"])["sha256"] == digest)
        comment = json_request(a, "POST", f"/api/assets/{asset_id}/comments", {"content": "验收评论"}, status=201)["comment"]
        check("comments create and read", len(a.require("GET", f"/api/assets/{asset_id}/comments")["comments"]) == 1)
        json_request(a, "DELETE", f"/api/comments/{comment['id']}", status=204)
        json_request(a, "DELETE", f"/api/assets/{legacy['id']}", status=204)
        check("soft deleted assets are hidden", a.request("GET", f"/api/assets/{legacy['id']}")[0] == 404)
        json_request(a, "POST", "/api/auth/logout", status=204)
        check("logout expires session", a.request("GET", "/api/auth/me")[0] == 401)
        json_request(a, "POST", "/api/auth/login",
                     {"email": receipt["accounts"][0]["email"], "password": password})
        check("login restores session", a.require("GET", "/api/auth/me")["user"]["id"] == receipt["accounts"][0]["id"])
        receipt["passed"] = True
        print(f"PASS all {len(receipt['checks'])} live checks", flush=True)
    finally:
        receipt["finishedAt"] = time.time()
        save_private(receipt_path, receipt)


if __name__ == "__main__":
    main()
