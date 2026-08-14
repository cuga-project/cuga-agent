#!/usr/bin/env python3
"""Verify Box works end-to-end — independent of CUGA/AP.

Mirrors the CUGA use case (the file/résumé watcher): UPLOAD a file, DOWNLOAD it back, verify the
bytes round-trip, then delete it. That proves the token can both see a folder's contents AND read a
file's content — which is exactly what the watcher hands to the agent.

Keys:   BOX_DEV_TOKEN   (or EVENTS_BOX_TOKEN)   — a Box Developer Token expires ~60 min
Extra:  BOX_FOLDER_ID   (optional — defaults to "0", the "All Files" root)
"""

import json
import sys
import time
import uuid

from _common import PASS, FAIL, SKIP, load_env, request, request_json, title, ok, bad, warn


def _multipart(fields, filename, content):
    boundary = "----cuga" + uuid.uuid4().hex
    body = b""
    for name, val in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n").encode()
    body += (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
        ).encode()
        + content
        + b"\r\n"
    )
    body += f"--{boundary}--\r\n".encode()
    return boundary, body


def main():
    env = load_env()
    title("Box")
    tok = env.get("BOX_DEV_TOKEN") or env.get("EVENTS_BOX_TOKEN")
    if not tok:
        warn("BOX_DEV_TOKEN not set — skipping")
        return SKIP

    h = {"Authorization": f"Bearer {tok}"}
    folder = env.get("BOX_FOLDER_ID", "0")

    st, _, j, _ = request_json("GET", "https://api.box.com/2.0/users/me", headers=h)
    if st != 200 or not j:
        bad(f"users/me failed (HTTP {st}) — dev token expired? (they last ~60 min)")
        return FAIL
    ok(f"token valid — {j.get('name')} <{j.get('login')}>")

    name = f"cuga-prereq-{int(time.time())}.txt"
    marker = f"cuga-prereq-{uuid.uuid4().hex}"
    attrs = json.dumps({"name": name, "parent": {"id": folder}})
    boundary, body = _multipart({"attributes": attrs}, name, marker.encode())
    uh = {**h, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    st, _, b = request("POST", "https://upload.box.com/api/2.0/files/content", headers=uh, data=body)
    if st not in (200, 201):
        bad(f"upload failed (HTTP {st}): {b[:200].decode(errors='replace')}")
        return FAIL
    fid = json.loads(b)["entries"][0]["id"]
    ok(f"uploaded {name} -> file id {fid} (folder {folder})")

    st, _, dl = request("GET", f"https://api.box.com/2.0/files/{fid}/content", headers=h)
    if st != 200 or dl != marker.encode():
        bad(f"download/verify failed (HTTP {st}) — content did not round-trip")
        request("DELETE", f"https://api.box.com/2.0/files/{fid}", headers=h)
        return FAIL
    ok("downloaded the file and content matches — the read path works")

    st, _, _ = request("DELETE", f"https://api.box.com/2.0/files/{fid}", headers=h)
    if st == 204:
        ok("cleaned up the test file")
    else:
        warn(f"could not delete test file {fid} (HTTP {st}) — remove it manually")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
