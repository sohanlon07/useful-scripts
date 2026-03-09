#!/usr/bin/env python3
"""
Add Bluesky profiles to a list via the AT Protocol API.

Usage:
    export BSKY_HANDLE="you.bsky.social"
    export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
    export BSKY_LIST_URL="https://bsky.app/profile/shaneohanlon.dev/lists/3lyuljkdqou23"

    python bsky_add_to_list.py profiles.csv

CSV format (one column, no header required — or a header row is auto-skipped if it
contains "url" or "handle"):
    https://bsky.app/profile/alice.bsky.social
    https://bsky.app/profile/bob.bsky.social
"""

import csv
import os
import sys
import time
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BSKY_API = "https://bsky.social/xrpc"


# ── Auth ──────────────────────────────────────────────────────────────────────

def create_session(handle: str, app_password: str) -> dict:
    resp = requests.post(
        f"{BSKY_API}/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()  # contains accessJwt, did, handle


# ── DID resolution ────────────────────────────────────────────────────────────

def resolve_handle(handle: str, token: str) -> str:
    """Resolve a handle to a DID."""
    resp = requests.get(
        f"{BSKY_API}/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["did"]


def profile_url_to_handle(url: str) -> str:
    """
    Extract handle or DID from a bsky.app profile URL.
    e.g. https://bsky.app/profile/alice.bsky.social → alice.bsky.social
    """
    url = url.strip().rstrip("/")
    parts = url.split("/profile/")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse profile URL: {url}")
    return parts[1].split("/")[0]


# ── List URL → AT URI ─────────────────────────────────────────────────────────

def list_url_to_at_uri(list_url: str, token: str) -> str:
    """
    Convert a bsky.app list URL to an AT URI (at://did/app.bsky.graph.list/rkey).
    e.g. https://bsky.app/profile/alice.bsky.social/lists/abc123
    """
    list_url = list_url.strip().rstrip("/")
    parts = list_url.split("/profile/")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse list URL: {list_url}")
    tail = parts[1].split("/lists/")
    if len(tail) != 2:
        raise ValueError(f"List URL has no /lists/ segment: {list_url}")
    handle, rkey = tail[0], tail[1]
    did = resolve_handle(handle, token) if not handle.startswith("did:") else handle
    return f"at://{did}/app.bsky.graph.list/{rkey}"


# ── Add member ────────────────────────────────────────────────────────────────

def add_list_member(actor_did: str, list_at_uri: str, author_did: str, token: str) -> dict:
    """Create an app.bsky.graph.listitem record."""
    record = {
        "$type": "app.bsky.graph.listitem",
        "subject": actor_did,
        "list": list_at_uri,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    resp = requests.post(
        f"{BSKY_API}/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "repo": author_did,
            "collection": "app.bsky.graph.listitem",
            "record": record,
        },
        timeout=15,
    )
    if resp.status_code == 400 and "already exists" in resp.text.lower():
        return {"status": "already_exists"}
    resp.raise_for_status()
    return resp.json()


# ── CSV parsing ───────────────────────────────────────────────────────────────

def load_profile_urls(csv_path: str) -> list[str]:
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:
                continue
            val = row[0].strip()
            # Skip header rows
            if i == 0 and val.lower() in ("url", "handle", "profile", "profile_url"):
                continue
            if val:
                urls.append(val)
    return urls


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: bsky_add_to_list.py <profiles.csv>")

    csv_path = sys.argv[1]

    handle = os.environ.get("BSKY_HANDLE")
    app_password = os.environ.get("BSKY_APP_PASSWORD")
    list_url = os.environ.get("BSKY_LIST_URL")

    missing = [k for k, v in {
        "BSKY_HANDLE": handle,
        "BSKY_APP_PASSWORD": app_password,
        "BSKY_LIST_URL": list_url,
    }.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    # Auth
    log.info("Authenticating as %s …", handle)
    session = create_session(handle, app_password)
    token = session["accessJwt"]
    author_did = session["did"]
    log.info("Authenticated (DID: %s)", author_did)

    # Resolve list AT URI once
    log.info("Resolving list URL …")
    list_at_uri = list_url_to_at_uri(list_url, token)
    log.info("List AT URI: %s", list_at_uri)

    # Load profiles
    profiles = load_profile_urls(csv_path)
    log.info("Loaded %d profile(s) from %s", len(profiles), csv_path)

    ok, skipped, failed = 0, 0, 0

    for url in profiles:
        try:
            raw_handle = profile_url_to_handle(url)
            actor_did = (
                raw_handle if raw_handle.startswith("did:")
                else resolve_handle(raw_handle, token)
            )
            result = add_list_member(actor_did, list_at_uri, author_did, token)
            if result.get("status") == "already_exists":
                log.info("SKIP (already in list): %s", url)
                skipped += 1
            else:
                log.info("ADDED: %s (%s)", url, actor_did)
                ok += 1
            # Stay well within rate limits (~500 writes/hr on ATP)
            time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            log.error("FAILED: %s — %s", url, exc)
            failed += 1

    log.info("Done. added=%d skipped=%d failed=%d", ok, skipped, failed)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()