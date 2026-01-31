import json
import uuid
import urllib.request

import streamlit as st

QDRANT_URL = "http://qdrant:6333"
COLLECTION_NAME = "streamlit_demo"

st.title("Hello, mittwald 👋")
st.write("Streamlit läuft im Container.")

text = st.text_input("Text")

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
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points",
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
