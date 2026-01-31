import json
import uuid
import urllib.request

import requests
import streamlit as st


def render_qdrant_tab() -> None:
    qdrant_url = "http://qdrant:6333"
    collection_name = "streamlit_demo"
    fastapi_url = "http://fastapi:8000"

    st.title("Hello, mittwald 👋")
    st.write("Streamlit läuft im Container.")

    text = st.text_input("Text")

    st.subheader("FastAPI backend")
    if st.button("FastAPI: hello world"):
        try:
            resp = requests.get(f"{fastapi_url}/", timeout=5)
            if resp.status_code == 200:
                st.success("FastAPI responded.")
                st.code(resp.text, language="json")
            else:
                st.error(f"FastAPI error: {resp.status_code} - {resp.text}")
        except Exception as exc:
            st.error(f"FastAPI request failed: {exc}")

    if st.button("FastAPI: demo upsert"):
        try:
            resp = requests.post(f"{fastapi_url}/qdrant/demo-upsert", timeout=10)
            if resp.status_code == 200:
                st.success("Demo upsert completed.")
                st.code(resp.text, language="json")
            else:
                st.error(f"FastAPI error: {resp.status_code} - {resp.text}")
        except Exception as exc:
            st.error(f"FastAPI request failed: {exc}")

    if st.button("FastAPI: demo search"):
        try:
            resp = requests.post(f"{fastapi_url}/qdrant/demo-search", timeout=10)
            if resp.status_code == 200:
                st.success("Demo search completed.")
                st.code(resp.text, language="json")
            else:
                st.error(f"FastAPI error: {resp.status_code} - {resp.text}")
        except Exception as exc:
            st.error(f"FastAPI request failed: {exc}")

    if st.button("Create Qdrant collection"):
        create_payload = {"vectors": {"size": 4, "distance": "Cosine"}}
        create_url = f"{qdrant_url}/collections/{collection_name}"
        try:
            create_resp = requests.put(create_url, json=create_payload, timeout=5)
            if create_resp.status_code in (200, 201):
                st.success(f"Collection '{collection_name}' is ready.")
            else:
                st.error(
                    f"Failed to create collection: {create_resp.status_code} - {create_resp.text}"
                )
        except Exception as exc:
            st.error(f"Failed to create collection: {exc}")

    if st.button("Send fake embedding to Qdrant"):
        fake_embedding = [0.1, 0.2, 0.3, 0.4]
        point_id = str(uuid.uuid4())
        payload = {
            "points": [
                {
                    "id": point_id,
                    "vector": fake_embedding,
                    "payload": {"text": text, "source": "streamlit-demo"},
                }
            ]
        }
        req = urllib.request.Request(
            f"{qdrant_url}/collections/{collection_name}/points",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )

        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
            st.success(f"Sent point {point_id} to Qdrant")
            st.code(body, language="json")
        except Exception as exc:
            st.error(f"Failed to send to Qdrant: {exc}")
