#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.request


def request_json(
    method: str, url: str, payload: dict | None = None, timeout: int = 5
):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise urllib.error.HTTPError(
            exc.url,
            exc.code,
            f"{exc.reason} (body={body})",
            exc.headers,
            exc.fp,
        ) from exc


def main() -> int:
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    health_url = f"{qdrant_url}/healthz"
    last_label = "health"
    last_url = health_url

    def call(label: str, method: str, url: str, payload: dict | None = None):
        nonlocal last_label, last_url
        last_label = label
        last_url = url
        return request_json(method, url, payload)

    try:
        with urllib.request.urlopen(health_url, timeout=5) as resp:
            if resp.status != 200:
                print(f"Unexpected status {resp.status} from {health_url}", file=sys.stderr)
                return 1
    except urllib.error.URLError as exc:
        print(f"Failed to reach {health_url}: {exc}", file=sys.stderr)
        return 1

    collection_name = "codex_qdrant_test"
    vector_size = 4

    try:
        call(
            "create_collection",
            "PUT",
            f"{qdrant_url}/collections/{collection_name}?wait=true",
            {
                "vectors": {
                    "size": vector_size,
                    "distance": "Cosine",
                }
            },
        )

        points = [
            {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"label": "alpha"}},
            {"id": 2, "vector": [0.2, 0.1, 0.0, 0.3], "payload": {"label": "beta"}},
            {"id": 3, "vector": [0.9, 0.8, 0.7, 0.6], "payload": {"label": "gamma"}},
        ]
        call(
            "upsert_points",
            "PUT",
            f"{qdrant_url}/collections/{collection_name}/points?wait=true",
            {"points": points},
        )

        try:
            retrieve_resp = call(
                "retrieve_points",
                "POST",
                f"{qdrant_url}/collections/{collection_name}/points/retrieve",
                {"ids": [1, 2, 3], "with_payload": True},
            )
            retrieved = retrieve_resp.get("result") if retrieve_resp else None
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            retrieved = []
            for point_id in (1, 2, 3):
                point_resp = call(
                    "retrieve_point_by_id",
                    "GET",
                    f"{qdrant_url}/collections/{collection_name}/points/{point_id}?with_payload=true",
                )
                if point_resp and point_resp.get("result"):
                    retrieved.append(point_resp["result"])

        if not retrieved or len(retrieved) != 3:
            print("Failed to retrieve inserted points.", file=sys.stderr)
            return 1

        search_resp = call(
            "search_points",
            "POST",
            f"{qdrant_url}/collections/{collection_name}/points/search",
            {"vector": [0.1, 0.2, 0.3, 0.4], "limit": 1, "with_payload": True},
        )
        search_result = search_resp.get("result") if search_resp else None
        if not search_result:
            print("Search returned no results.", file=sys.stderr)
            return 1
    except urllib.error.URLError as exc:
        print(
            f"Qdrant request failed during {last_label} ({last_url}): {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        try:
            request_json("DELETE", f"{qdrant_url}/collections/{collection_name}")
        except urllib.error.URLError:
            pass

    top = search_result[0]
    print(f"Inserted {len(points)} points and retrieved them successfully.")
    print(f"Search top result id={top.get('id')} payload={top.get('payload')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
