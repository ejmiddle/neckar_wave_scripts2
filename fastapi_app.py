import json
import os
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException

app = FastAPI()

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333").rstrip("/")
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "fastapi_demo")
VECTOR_SIZE = 4


def request_json(method: str, url: str, payload: dict | None = None, timeout: int = 5):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        if not body:
            return None
        return json.loads(body.decode("utf-8"))


@app.get("/")
def hello_world():
    return {"message": "hello world"}


@app.post("/qdrant/demo-upsert")
def qdrant_demo_upsert():
    try:
        try:
            request_json(
                "PUT",
                f"{QDRANT_URL}/collections/{COLLECTION_NAME}?wait=true",
                {"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                raise

        points = [
            {"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"label": "alpha"}},
            {"id": 2, "vector": [0.2, 0.1, 0.0, 0.3], "payload": {"label": "beta"}},
            {"id": 3, "vector": [0.9, 0.8, 0.7, 0.6], "payload": {"label": "gamma"}},
        ]
        upsert_resp = request_json(
            "PUT",
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points?wait=true",
            {"points": points},
        )
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant unreachable: {exc}") from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise HTTPException(
            status_code=exc.code,
            detail=f"Qdrant error: {exc.reason} (body={body})",
        ) from exc
    return {"collection": COLLECTION_NAME, "inserted": len(points), "result": upsert_resp}


@app.post("/qdrant/demo-search")
def qdrant_demo_search():
    try:
        search_resp = request_json(
            "POST",
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
            {"vector": [0.1, 0.2, 0.3, 0.4], "limit": 3, "with_payload": True},
        )
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant unreachable: {exc}") from exc
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        raise HTTPException(
            status_code=exc.code,
            detail=f"Qdrant error: {exc.reason} (body={body})",
        ) from exc
    return {"collection": COLLECTION_NAME, "result": search_resp}
