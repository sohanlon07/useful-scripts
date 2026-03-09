#!/usr/bin/env python3
"""
Add Bluesky profiles to a list, with optional follow.

Two modes — select via env var BSKY_MODE:

  csv  (default)
       Read profile URLs from a CSV file passed as a positional argument,
       follow each account if not already followed, and add to your list.

       export BSKY_MODE=csv                         # or omit — csv is default
       export BSKY_HANDLE="you.bsky.social"
       export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
       export BSKY_LIST_URL="https://bsky.app/profile/you.bsky.social/lists/abc123"
       python bsky_add_to_list.py profiles.csv

  list
       Read all members from an existing source list (owned by anyone),
       follow each account if not already followed, and add to your list.

       export BSKY_MODE=list
       export BSKY_HANDLE="you.bsky.social"
       export BSKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
       export BSKY_LIST_URL="https://bsky.app/profile/you.bsky.social/lists/abc123"
       export BSKY_SOURCE_LIST_URL="https://bsky.app/profile/someone.bsky.social/lists/xyz789"
       python bsky_add_to_list.py        # no positional arg needed in list mode

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
    return resp.json()  # contains accessJwt, did, handle


# ── DID / handle resolution ───────────────────────────────────────────────────

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
    e.g. https://bsky.app/profile/alice.bsky.social -> alice.bsky.social
    """
    url = url.strip().rstrip("/")
    parts = url.split("/profile/")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse profile URL: {url}")
    return parts[1].split("/")[0]


# ── List URL -> AT URI ────────────────────────────────────────────────────────

def list_url_to_at_uri(list_url: str, token: str) -> str:
    """
    Convert a bsky.app list URL to an AT URI.
    e.g. https://bsky.app/profile/alice.bsky.social/lists/abc123
      -> at://did:plc:.../app.bsky.graph.list/abc123
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


# ── Source list fetching ──────────────────────────────────────────────────────

def fetch_list_members(list_at_uri: str, token: str) -> list[str]:
    """
    Page through app.bsky.graph.getList and return all member DIDs.
    """
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

        time.sleep(0.1)  # polite paging

    return dids


# ── Follow ────────────────────────────────────────────────────────────────────

def is_following(actor_did: str, token: str) -> bool:
    """Check if the authed user already follows actor."""
    resp = requests.get(
        f"{BSKY_API}/app.bsky.actor.getProfile",
        params={"actor": actor_did},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    viewer = resp.json().get("viewer", {})
    # 'following' is set to the follow record AT URI if we follow them
    return bool(viewer.get("following"))


def follow_account(actor_did: str, author_did: str, token: str) -> dict:
    """Create an app.bsky.graph.follow record."""
    record = {
        "$type": "app.bsky.graph.follow",
        "subject": actor_did,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    resp = requests.post(
        f"{BSKY_API}/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "repo": author_did,
            "collection": "app.bsky.graph.follow",
            "record": record,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── Add list member ───────────────────────────────────────────────────────────

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
            if i == 0 and val.lower() in ("url", "handle", "profile", "profile_url"):
                continue
            if val:
                urls.append(val)
    return urls


# ── Shared processing loop ────────────────────────────────────────────────────

def process_dids(dids: list[str], list_at_uri: str, author_did: str, token: str) -> None:
    """Follow (if needed) and add to list for a sequence of DIDs."""
    list_added, list_skipped, followed, follow_skipped, failed = 0, 0, 0, 0, 0

    for actor_did in dids:
        try:
            # ── Add to list ──
            result = add_list_member(actor_did, list_at_uri, author_did, token)
            if result.get("status") == "already_exists":
                log.info("SKIP list (already member): %s", actor_did)
                list_skipped += 1
            else:
                log.info("ADDED to list: %s", actor_did)
                list_added += 1
            time.sleep(0.25)

            # ── Follow if not already following ──
            if is_following(actor_did, token):
                log.info("SKIP follow (already following): %s", actor_did)
                follow_skipped += 1
            else:
                follow_account(actor_did, author_did, token)
                log.info("FOLLOWED: %s", actor_did)
                followed += 1

            time.sleep(0.25)  # stay within ~500 writes/hr ATP rate limit

        except Exception as exc:  # noqa: BLE001
            log.error("FAILED: %s -- %s", actor_did, exc)
            failed += 1

    log.info(
        "Done. list_added=%d list_skipped=%d followed=%d follow_skipped=%d failed=%d",
        list_added, list_skipped, followed, follow_skipped, failed,
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
            sys.exit("Usage (csv mode): bsky_add_to_list.py <profiles.csv>")
        csv_path = sys.argv[1]
        profile_urls = load_profile_urls(csv_path)
        log.info("Loaded %d profile(s) from %s", len(profile_urls), csv_path)

        # Resolve handles/URLs -> DIDs
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

    process_dids(dids, dest_list_at_uri, author_did, token)


if __name__ == "__main__":
    main()