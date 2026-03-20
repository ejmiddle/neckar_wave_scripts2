import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

DEFAULT_MODELS = [
    # Stable / GA (Vertex AI list shows these as stable model IDs)
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    # Preview (Gemini 3 series)
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
]


def build_schema():
    """
    A simple example schema for 'information extraction from an image'
    (tweak to your use case: receipts, IDs, invoices, etc.).
    """
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["name", "value"],
                },
            },
        },
        "required": ["summary", "fields"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-2.5-pro", choices=DEFAULT_MODELS)
    parser.add_argument("--image", required=True, help="Path to a local image (png/jpg/webp).")
    parser.add_argument(
        "--prompt",
        default="Extract all useful information from this image. "
                "If it is a document, extract key-value fields and any totals/dates/IDs.",
        help="Extraction instruction.",
    )
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    # Guess MIME type by extension (good enough for a basic test)
    ext = image_path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext)
    if not mime:
        raise ValueError(f"Unsupported image extension: {ext}")

    image_bytes = image_path.read_bytes()

    load_dotenv()

    # If you want Vertex AI instead of API-key auth, set:
    #   export GOOGLE_GENAI_USE_VERTEXAI=1
    # and ensure ADC is configured (gcloud auth application-default login)
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )

    schema = build_schema()

    # Ask for JSON output that matches our schema
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        # You can also experiment with thinking levels on Gemini 3 preview models:
        # thinking_config=types.ThinkingConfig(thinking_level="low"),
    )

    contents = [
        types.Part.from_text(text=args.prompt),
        types.Part.from_bytes(data=image_bytes, mime_type=mime),
    ]

    resp = client.models.generate_content(
        model=args.model,
        contents=contents,
        config=config,
    )

    # The SDK often provides resp.text as the generated text; in JSON mode it should be JSON.
    raw = resp.text or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: print raw output so you can see what the model produced
        print(raw)
        raise

    print(json.dumps(parsed, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
