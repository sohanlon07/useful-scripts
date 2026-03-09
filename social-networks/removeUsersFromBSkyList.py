#!/usr/bin/env python3
"""
Remove Bluesky profiles from a list via the AT Protocol API.

Two modes — select via env var BSKY_MODE:

  csv  (default)
       Read profile URLs from a CSV file passed as a positional argument
       and remove each from your list.

       export BSKY_MODE=csv                         # or omit — csv is default
       export BSKY_HANDLE="you.bsky.social"
       export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
       export BSKY_LIST_URL="https://bsky.app/profile/you.bsky.social/lists/abc123"
       python bsky_remove_from_list.py profiles.csv

  list
       Read all members from a source list and remove them from your list.

       export BSKY_MODE=list
       export BSKY_HANDLE="you.bsky.social"
       export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
       export BSKY_LIST_URL="https://bsky.app/profile/you.bsky.social/lists/abc123"
       export BSKY_SOURCE_LIST_URL="https://bsky.app/profile/someone.bsky.social/lists/xyz789"
       python bsky_remove_from_list.py

CSV format (one column, header row auto-skipped):
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
    return resp.json()


# ── DID / handle resolution ───────────────────────────────────────────────────

def resolve_handle(handle: str, token: str) -> str:
    resp = requests.get(
        f"{BSKY_API}/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["did"]


def profile_url_to_handle(url: str) -> str:
    url = url.strip().rstrip("/")
    parts = url.split("/profile/")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse profile URL: {url}")
    return parts[1].split("/")[0]


# ── List URL -> AT URI ────────────────────────────────────────────────────────

def list_url_to_at_uri(list_url: str, token: str) -> str:
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


# ── List fetching ─────────────────────────────────────────────────────────────

def fetch_list_members(list_at_uri: str, token: str) -> list[str]:
    """Page through getList and return all member DIDs."""
    dids = []
    cursor = None
    while True:
        params = {"list": list_at_uri, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{BSKY_API}/app.bsky.graph.getList",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", []):
            did = item.get("subject", {}).get("did")
            if did:
                dids.append(did)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.1)
    return dids


def fetch_list_item_records(list_at_uri: str, author_did: str, token: str) -> dict[str, str]:
    """
    Page through getList and return a mapping of {subject_did: record_rkey}
    for the given list. We need the rkey to delete the listitem record.
    """
    did_to_rkey = {}
    cursor = None
    while True:
        params = {"list": list_at_uri, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{BSKY_API}/app.bsky.graph.getList",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", []):
            subject_did = item.get("subject", {}).get("did")
            # uri format: at://did/app.bsky.graph.listitem/rkey
            uri = item.get("uri", "")
            rkey = uri.split("/")[-1] if uri else None
            if subject_did and rkey:
                did_to_rkey[subject_did] = rkey
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.1)
    return did_to_rkey


# ── Remove list member ────────────────────────────────────────────────────────

def remove_list_member(rkey: str, author_did: str, token: str) -> None:
    """Delete the app.bsky.graph.listitem record by rkey."""
    resp = requests.post(
        f"{BSKY_API}/com.atproto.repo.deleteRecord",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "repo": author_did,
            "collection": "app.bsky.graph.listitem",
            "rkey": rkey,
        },
        timeout=15,
    )
    resp.raise_for_status()


# ── CSV parsing ───────────────────────────────────────────────────────────────

def load_profile_urls(csv_path: str) -> list[str]:
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:
                continue
            val = row[0].strip()
            if i == 0 and val.lower() in ("url", "handle", "profile", "profile_url"):
                continue
            if val:
                urls.append(val)
    return urls


# ── Shared processing loop ────────────────────────────────────────────────────

def process_removals(
    dids: list[str],
    dest_list_at_uri: str,
    author_did: str,
    token: str,
) -> None:
    """Remove each DID from the destination list if present."""

    log.info("Fetching current members of destination list ...")
    did_to_rkey = fetch_list_item_records(dest_list_at_uri, author_did, token)
    log.info("Destination list has %d current member(s)", len(did_to_rkey))

    removed, skipped, failed = 0, 0, 0

    for actor_did in dids:
        try:
            rkey = did_to_rkey.get(actor_did)
            if not rkey:
                log.info("SKIP (not in list): %s", actor_did)
                skipped += 1
                continue

            remove_list_member(rkey, author_did, token)
            log.info("REMOVED: %s", actor_did)
            removed += 1
            time.sleep(0.25)  # stay within ~500 writes/hr ATP rate limit

        except Exception as exc:  # noqa: BLE001
            log.error("FAILED: %s -- %s", actor_did, exc)
            failed += 1

    log.info(
        "Done. removed=%d skipped=%d failed=%d",
        removed, skipped, failed,
    )
    if failed:
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    mode = os.environ.get("BSKY_MODE", "csv").strip().lower()

    if mode not in ("csv", "list"):
        sys.exit("BSKY_MODE must be 'csv' or 'list'")

    handle       = os.environ.get("BSKY_HANDLE")
    app_password = os.environ.get("BSKY_APP_PASSWORD")
    list_url     = os.environ.get("BSKY_LIST_URL")

    required = {
        "BSKY_HANDLE": handle,
        "BSKY_APP_PASSWORD": app_password,
        "BSKY_LIST_URL": list_url,
    }
    if mode == "list":
        required["BSKY_SOURCE_LIST_URL"] = os.environ.get("BSKY_SOURCE_LIST_URL")

    missing = [k for k, v in required.items() if not v]
    if missing:
        sys.exit(f"Missing env vars: {', '.join(missing)}")

    # Auth
    log.info("Authenticating as %s ...", handle)
    session    = create_session(handle, app_password)
    token      = session["accessJwt"]
    author_did = session["did"]
    log.info("Authenticated (DID: %s)", author_did)

    # Resolve destination list AT URI
    log.info("Resolving destination list ...")
    dest_list_at_uri = list_url_to_at_uri(list_url, token)
    log.info("Destination list AT URI: %s", dest_list_at_uri)

    if mode == "csv":
        if len(sys.argv) != 2:
            sys.exit("Usage (csv mode): bsky_remove_from_list.py <profiles.csv>")
        csv_path = sys.argv[1]
        profile_urls = load_profile_urls(csv_path)
        log.info("Loaded %d profile(s) from %s", len(profile_urls), csv_path)

        dids = []
        for url in profile_urls:
            raw = profile_url_to_handle(url)
            dids.append(raw if raw.startswith("did:") else resolve_handle(raw, token))

    else:  # mode == "list"
        source_list_url = os.environ.get("BSKY_SOURCE_LIST_URL")
        log.info("Resolving source list ...")
        source_list_at_uri = list_url_to_at_uri(source_list_url, token)
        log.info("Source list AT URI: %s", source_list_at_uri)

        log.info("Fetching source list members ...")
        dids = fetch_list_members(source_list_at_uri, token)
        log.info("Found %d member(s) in source list", len(dids))

    process_removals(dids, dest_list_at_uri, author_did, token)


if __name__ == "__main__":
    main()