import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.app_paths import DATA_DIR
from src.logging_config import logger
from src.notion_access import (
    DEFAULT_ORDER_DB_TITLE,
    build_order_database_properties,
    create_order_database,
    insert_orders,
)
from src.order_prompt_config import (
    DEFAULT_ALLOWED_VALUES,
    DEFAULT_FIELD_DESCRIPTIONS,
    DEFAULT_OUTPUT_SCHEMA,
    DEFAULT_SYSTEM_PROMPT,
    apply_default_eintragender,
    build_system_prompt_with_descriptions,
    load_product_list,
    load_prompt_config,
    save_prompt_config,
)

DEFAULT_NOTION_PAGE_ID = "3014e28bdf9e802183d3efda2854f233"
# Fill this once the database exists, to skip re-creating it.
HARDCODED_NOTION_DATABASE_ID = "3014e28bdf9e812c93e7e970dd3146b1"

st.title("🧾 Bestellungen erfassen")

audio_col, eintragender_col, reset_col = st.columns([4, 3, 1], vertical_alignment="bottom")
if "audio_reset" not in st.session_state:
    st.session_state["audio_reset"] = 0
with audio_col:
    audio_data = st.audio_input(
        "Spracheingabe",
        help="Sprich direkt ins Mikrofon und speichere die Aufnahme.",
        key=f"audio_data_{st.session_state['audio_reset']}",
    )
with eintragender_col:
    default_eintragender = st.text_input(
        "Standard: Eintragender",
        value=st.session_state.get("default_eintragender", ""),
        help="Wird als Default für Eintragender genutzt, wenn kein Wert erkannt wurde.",
    )
    st.session_state["default_eintragender"] = default_eintragender
with reset_col:
    if st.button("Reset", use_container_width=True):
        st.session_state["audio_reset"] += 1
        st.session_state.pop("transcript_text", None)
        st.session_state.pop("transcript_display", None)
        st.session_state.pop("orders_json", None)
        st.session_state.pop("orders_prompt_payload", None)
        st.session_state.pop("orders_output_template", None)



with st.sidebar:
    debugging_mode = st.checkbox("Debugging mode", value=False)

    with st.expander("⚙️ Transkriptions-Defaults", expanded=False):
        mode = st.radio(
            "Transkriptionsmodus",
            ["Lokal (Whisper)", "OpenAI API"],
            index=1,
            horizontal=True,
            help="Lokal nutzt das Whisper-Modell auf diesem Rechner. API nutzt OpenAI.",
        )

        if mode == "Lokal (Whisper)":
            model_choice = st.selectbox(
                "Transkriptionsmodell (Lokal)",
                ["tiny", "base", "small", "medium", "large"],
                index=1,
                help="Größere Modelle sind genauer, aber langsamer.",
            )
            api_model_choice = "gpt-4o-mini-transcribe"
            api_prompt = ""
        else:
            api_model_choice = st.selectbox(
                "Transkriptionsmodell (API)",
                ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "gpt-4o-transcribe-diarize", "whisper-1"],
                index=0,
                help="API-Modelle für Speech-to-Text.",
            )
            api_prompt = st.text_input(
                "Prompt (optional)",
                help="Optionaler Kontext, um Fachbegriffe korrekt zu transkribieren.",
            )
            model_choice = "base"

    with st.expander("🧹 Session-Reset", expanded=False):
        if st.button("Transkript löschen"):
            st.session_state.pop("transcript_text", None)
            st.session_state.pop("transcript_display", None)
        if st.button("Extraktion löschen"):
            st.session_state.pop("orders_json", None)
            st.session_state.pop("orders_prompt_payload", None)
        if st.button("Alles löschen"):
            for key in [
                "transcript_text",
                "transcript_display",
                "orders_json",
                "orders_prompt_payload",
                "orders_output_template",
            ]:
                st.session_state.pop(key, None)


@st.cache_resource(show_spinner=False)
def _load_whisper(model_name: str):
    import whisper

    return whisper.load_model(model_name)


def _transcribe_audio(file_path: Path, model_name: str) -> str:
    model = _load_whisper(model_name)
    result = model.transcribe(str(file_path))
    return result.get("text", "").strip()


def _transcribe_audio_api(file_path: Path, model_name: str, prompt: str | None) -> str:
    from openai import OpenAI

    api_key = _get_openai_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY nicht gefunden. Bitte in .env oder st.secrets setzen."
        )
    client = OpenAI(api_key=api_key)
    kwargs = {"model": model_name, "file": open(file_path, "rb"), "response_format": "text"}
    if prompt:
        kwargs["prompt"] = prompt
    with kwargs["file"] as audio_file:
        kwargs["file"] = audio_file
        transcription = client.audio.transcriptions.create(**kwargs)
    return transcription.text if hasattr(transcription, "text") else str(transcription).strip()


def _get_openai_api_key() -> str | None:
    try:
        secrets_key = st.secrets.get("OPENAI_API_KEY", None)
    except Exception:
        secrets_key = None

    return (
        st.session_state.get("openai_api_key")
        or os.getenv("OPENAI_API_KEY")
        or secrets_key
    )


def _normalize_orders_for_json(orders: list[dict]) -> list[dict]:
    normalized = []
    for order in orders:
        entry = dict(order)
        datum_value = entry.get("Datum")
        if isinstance(datum_value, datetime):
            entry["Datum"] = datum_value.date().isoformat()
        elif isinstance(datum_value, date):
            entry["Datum"] = datum_value.isoformat()
        normalized.append(entry)
    return normalized


def _orders_for_editor(orders: list[dict]) -> list[dict]:
    prepared = []
    for order in orders:
        entry = dict(order)
        datum_value = entry.get("Datum")
        if isinstance(datum_value, str) and datum_value:
            try:
                entry["Datum"] = date.fromisoformat(datum_value)
            except ValueError:
                entry["Datum"] = None
        prepared.append(entry)
    return prepared


def _orders_to_editor_df(orders: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(orders)
    if "Datum" not in df.columns:
        df["Datum"] = pd.NaT
    df["Datum"] = df["Datum"].replace("", pd.NA)
    df["Datum"] = pd.to_datetime(df["Datum"], errors="coerce").dt.date
    return df


def _extract_orders_api(
    transcript_text: str,
    model_name: str,
    output_template: dict,
    system_prompt_base: str,
    allowed_values: dict,
    field_descriptions: dict,
    default_eintragender: str = "",
) -> tuple[dict, dict | None]:
    from openai import OpenAI
    debug = True

    api_key = _get_openai_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY nicht gefunden. Bitte in .env oder st.secrets setzen."
        )

    client = OpenAI(api_key=api_key)
    output_structure = output_template or DEFAULT_OUTPUT_SCHEMA
    system_prompt = build_system_prompt_with_descriptions(
        system_prompt_base,
        output_structure,
        allowed_values,
        field_descriptions,
    )
    user_prompt = (
        "Transkription:\n"
        f"{transcript_text}\n\n"
        "Hinweis: Es kann mehrere Bestellungen geben. "
        "Wenn Felder fehlen, nutze die Default-Werte aus der Struktur."
    )
    logger.info("OpenAI extraction request model=%s", model_name)
    logger.info("OpenAI extraction system prompt:\n%s", system_prompt)
    logger.info("OpenAI extraction user prompt:\n%s", user_prompt)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning(
            "OpenAI extraction JSON parse failed; content length=%s",
            len(content),
        )
        # Fallback: handle multiple JSON objects concatenated
        try:
            decoder = json.JSONDecoder()
            first_obj, _ = decoder.raw_decode(content)
            parsed = first_obj
        except Exception:
            logger.error(
                "OpenAI extraction JSON raw_decode failed; content preview=%s",
                content[:2000],
            )
            parsed = {"orders": [], "notes": "JSON-Parsing fehlgeschlagen", "raw": content}

    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]

    if default_eintragender:
        for order in parsed.get("orders", []):
            if not order.get("Eintragender"):
                order["Eintragender"] = default_eintragender

    if debug:
        return parsed, {"system": system_prompt, "user": user_prompt}
    return parsed, None


def _run_transcription(
    audio_payload,
    transcribe_mode: str,
    local_model: str,
    api_model: str,
    api_prompt_text: str,
) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_file.write(audio_payload.getvalue())
        tmp_path = Path(tmp_file.name)
    try:
        if transcribe_mode == "Lokal (Whisper)":
            transcript_text = _transcribe_audio(tmp_path, local_model)
        else:
            product_values = DEFAULT_ALLOWED_VALUES.get("Produkt", [])
            if product_values:
                product_hint = "Produktliste: " + ", ".join(product_values)
                if api_prompt_text:
                    api_prompt_text = f"{api_prompt_text}\n{product_hint}"
                else:
                    api_prompt_text = product_hint
            logger.info("OpenAI transcription prompt:\n%s", api_prompt_text or "")
            transcript_text = _transcribe_audio_api(tmp_path, api_model, api_prompt_text or None)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    return transcript_text


def _run_extraction(
    transcript_text: str,
    model_name: str,
    output_template: dict,
    system_prompt_base: str,
    allowed_values: dict,
    field_descriptions: dict,
    default_eintragender_value: str,
) -> tuple[dict, dict | None]:
    orders_json, prompt_payload = _extract_orders_api(
        transcript_text,
        model_name,
        output_template,
        system_prompt_base,
        allowed_values,
        field_descriptions,
        default_eintragender=default_eintragender_value,
    )
    return orders_json, prompt_payload

if debugging_mode:
    with st.expander("🔍 Debug: .env / Env-Status", expanded=False):
        repo_env = Path(__file__).resolve().parents[1] / ".env"
        st.write(f"Repo .env Pfad: {repo_env}")
        st.write(f"Repo .env existiert: {repo_env.exists()}")
        st.write(f"OPENAI_API_KEY in os.environ: {'OPENAI_API_KEY' in os.environ}")
        st.write(f"OPENAI_API_KEY Länge: {len(os.getenv('OPENAI_API_KEY') or '')}")
        st.write(f"Arbeitsverzeichnis: {os.getcwd()}")
        try:
            secrets_present = "OPENAI_API_KEY" in st.secrets
        except Exception as exc:
            secrets_present = f"secrets error: {exc}"
        st.write(f"OPENAI_API_KEY in st.secrets: {secrets_present}")


if audio_data is not None:
    st.audio(audio_data)

    if st.button("Transkribieren"):
        with st.spinner("Transkribiere Audio…"):
            try:
                transcript_text = st.session_state.get("transcript_text", "")
                if not transcript_text:
                    transcript_text = _run_transcription(
                        audio_data,
                        mode,
                        model_choice,
                        api_model_choice,
                        api_prompt,
                    )
                if transcript_text:
                    st.session_state["transcript_text"] = transcript_text
                    st.text_area(
                        "Transkript",
                        transcript_text,
                        height=180,
                        key="transcript_preview",
                    )
            except ModuleNotFoundError:
                if mode == "Lokal (Whisper)":
                    logger.error("Whisper module missing for local transcription.")
                    st.error(
                        "Whisper ist nicht installiert. Bitte `openai-whisper` installieren, "
                        "z.B. `uv pip install openai-whisper`."
                    )
                else:
                    logger.error("OpenAI SDK missing for API transcription.")
                    st.error(
                        "OpenAI SDK ist nicht installiert. Bitte `openai` installieren, "
                        "z.B. `uv pip install openai`."
                    )
            except Exception as exc:
                logger.exception("Transkription fehlgeschlagen.")
                st.error(f"Transkription fehlgeschlagen: {exc}")

if st.session_state.get("transcript_text"):
    st.text_area(
        "Transkript",
        st.session_state.get("transcript_text", ""),
        height=180,
        key="transcript_display",
    )


prompt_config = st.session_state.get("order_prompt_config") or load_prompt_config()
st.session_state["order_prompt_config"] = prompt_config

with st.expander("🧠 Prompt-Konfiguration (Helper)", expanded=False):
    st.caption("Änderungen wirken sich direkt auf die Extraktion aus.")
    system_prompt_input = st.text_area(
        "System-Prompt (Allgemeine Instruktionen)",
        value=prompt_config.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
        height=160,
    )
    output_schema_input = st.text_area(
        "Ausgabe-Struktur (JSON)",
        value=json.dumps(
            prompt_config.get("output_schema", DEFAULT_OUTPUT_SCHEMA),
            ensure_ascii=True,
            indent=2,
        ),
        height=220,
    )
    allowed_values_input = st.text_area(
        "Erlaubte Werte (JSON)",
        value=json.dumps(
            prompt_config.get("allowed_values", DEFAULT_ALLOWED_VALUES),
            ensure_ascii=True,
            indent=2,
        ),
        height=160,
    )
    field_descriptions_input = st.text_area(
        "Feld-Erklärungen (JSON)",
        value=json.dumps(
            prompt_config.get("field_descriptions", DEFAULT_FIELD_DESCRIPTIONS),
            ensure_ascii=True,
            indent=2,
        ),
        height=160,
    )

    if st.button("Prompt-Konfiguration speichern"):
        try:
            output_schema_value = (
                json.loads(output_schema_input) if output_schema_input.strip() else {}
            )
            allowed_values_value = (
                json.loads(allowed_values_input) if allowed_values_input.strip() else {}
            )
            field_descriptions_value = (
                json.loads(field_descriptions_input)
                if field_descriptions_input.strip()
                else {}
            )
        except json.JSONDecodeError as exc:
            logger.warning("Prompt-Konfiguration JSON invalid: %s", exc)
            st.error(f"Ungültiges JSON: {exc}")
        else:
            new_config = {
                "system_prompt": system_prompt_input.strip() or DEFAULT_SYSTEM_PROMPT,
                "output_schema": output_schema_value or DEFAULT_OUTPUT_SCHEMA,
                "allowed_values": allowed_values_value,
                "field_descriptions": field_descriptions_value,
            }
            save_prompt_config(new_config)
            st.session_state["order_prompt_config"] = new_config
            st.success("Prompt-Konfiguration gespeichert.")
    if st.button("Prompt-Konfiguration zurücksetzen"):
        default_config = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "output_schema": DEFAULT_OUTPUT_SCHEMA,
            "allowed_values": DEFAULT_ALLOWED_VALUES,
            "field_descriptions": DEFAULT_FIELD_DESCRIPTIONS,
        }
        save_prompt_config(default_config)
        st.session_state["order_prompt_config"] = default_config
        st.success("Defaults gespeichert. Seite ggf. neu laden.")

    base_system_prompt = prompt_config.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    allowed_values = prompt_config.get("allowed_values", DEFAULT_ALLOWED_VALUES)
    field_descriptions = prompt_config.get(
        "field_descriptions", DEFAULT_FIELD_DESCRIPTIONS
    )
    output_template = apply_default_eintragender(
        prompt_config.get("output_schema", DEFAULT_OUTPUT_SCHEMA),
        default_eintragender,
    )
    st.session_state["orders_output_template"] = output_template

    extract_model = st.selectbox(
        "Extraktionsmodell (API)",
        ["gpt-4o-mini", "gpt-4o"],
        index=0,
        help="Modelle zur strukturierten Extraktion aus dem Transkript.",
    )


    st.header("Extraktions Infos:")
    st.json(output_template)
    st.json(allowed_values or {})
    st.json(field_descriptions or {})

if audio_data is not None and st.button("Transkribieren + extrahieren"):
    with st.spinner("Transkribiere und extrahiere…"):
        try:
            transcript_text = st.session_state.get("transcript_text", "")
            if not transcript_text:
                transcript_text = _run_transcription(
                    audio_data,
                    mode,
                    model_choice,
                    api_model_choice,
                    api_prompt,
                )
            if transcript_text:
                st.session_state["transcript_text"] = transcript_text
                orders_json, prompt_payload = _run_extraction(
                    transcript_text,
                    extract_model,
                    st.session_state.get("orders_output_template"),
                    base_system_prompt,
                    allowed_values,
                    field_descriptions,
                    default_eintragender,
                )
                st.session_state["orders_json"] = orders_json
                st.session_state["orders_prompt_payload"] = prompt_payload
            else:
                st.warning("Kein Text erkannt. Bitte mit klarer Sprache erneut versuchen.")
        except ModuleNotFoundError:
            if mode == "Lokal (Whisper)":
                logger.error("Whisper module missing for local transcription.")
                st.error(
                    "Whisper ist nicht installiert. Bitte `openai-whisper` installieren, "
                    "z.B. `uv pip install openai-whisper`."
                )
            else:
                logger.error("OpenAI SDK missing for API transcription.")
                st.error(
                    "OpenAI SDK ist nicht installiert. Bitte `openai` installieren, "
                    "z.B. `uv pip install openai`."
                )
        except Exception as exc:
            logger.exception("Transkription/Extraktion fehlgeschlagen.")
            st.error(f"Transkription/Extraktion fehlgeschlagen: {exc}")




current_orders = None
if st.session_state.get("orders_json", {}).get("orders"):
    st.subheader("✏️ Bestellungen bearbeiten")
    edited_orders = st.data_editor(
        _orders_to_editor_df(st.session_state["orders_json"]["orders"]),
        num_rows="dynamic",
        width="stretch",
        key="orders_editor",
    )
    current_orders = edited_orders.to_dict(orient="records")
    if st.button("Änderungen übernehmen"):
        st.session_state["orders_json"]["orders"] = _normalize_orders_for_json(current_orders)
        st.success("Änderungen gespeichert.")

if st.session_state.get("orders_json"):
    if current_orders is None:
        current_orders = st.session_state["orders_json"].get("orders", [])
    current_json = dict(st.session_state["orders_json"])
    current_json["orders"] = _normalize_orders_for_json(current_orders)

    st.caption("Erstellt eine neue Notion-Datenbank auf einer Seite und speichert alle Bestellungen.")
    notion_page_id = st.text_input(
        "Notion Page ID (für neue Datenbank)",
        value=st.session_state.get("notion_page_id", DEFAULT_NOTION_PAGE_ID),
        help="Die Seite, auf der die neue Datenbank erstellt werden soll.",
    )
    st.session_state["notion_page_id"] = notion_page_id

    use_hardcoded = st.checkbox(
        "Hardcoded Database ID verwenden",
        value=bool(HARDCODED_NOTION_DATABASE_ID),
        help="Aktiviert die im Code gesetzte Datenbank-ID.",
    )
    default_db_id = (
        HARDCODED_NOTION_DATABASE_ID
        if use_hardcoded and HARDCODED_NOTION_DATABASE_ID
        else st.session_state.get("notion_db_id", "")
    )
    notion_db_id = st.text_input(
        "Existing Notion Database ID (optional)",
        value=default_db_id,
        help="Falls vorhanden, werden Bestellungen direkt in diese Datenbank geschrieben.",
    )
    st.session_state["notion_db_id"] = notion_db_id

    default_title = f"{DEFAULT_ORDER_DB_TITLE} {date.today().strftime('%d.%m.%Y')}"
    db_title = st.text_input(
        "Neuer Datenbank-Titel",
        value=st.session_state.get("notion_db_title", default_title),
    )
    st.session_state["notion_db_title"] = db_title

    if st.button("Datenbank erstellen"):
        if not notion_page_id.strip():
            st.error("Bitte eine Notion Page ID angeben.")
        else:
            with st.spinner("Erstelle Notion-Datenbank…"):
                try:
                    created = create_order_database(
                        page_id=notion_page_id.strip(),
                        title=db_title.strip() or DEFAULT_ORDER_DB_TITLE,
                        properties=build_order_database_properties(),
                    )
                    created_id = created.get("id")
                    st.session_state["notion_db_id"] = created_id or ""
                    st.success(f"Datenbank erstellt: {created_id}")
                except Exception as exc:
                    logger.exception("Notion-Datenbank-Erstellung fehlgeschlagen.")
                    st.error(f"Datenbank-Erstellung fehlgeschlagen: {exc}")

    if st.button("Bestellungen in Notion speichern"):
        orders = current_json.get("orders", [])
        if not orders:
            st.warning("Keine Bestellungen gefunden.")
        elif not st.session_state.get("notion_db_id"):
            st.error("Bitte eine Datenbank-ID angeben oder zuerst eine Datenbank erstellen.")
        else:
            with st.spinner("Schreibe Bestellungen nach Notion…"):
                try:
                    count = insert_orders(st.session_state["notion_db_id"], orders)
                    st.success(f"{count} Bestellungen gespeichert.")
                except Exception as exc:
                    logger.exception("Notion-Export fehlgeschlagen.")
                    st.error(f"Notion-Export fehlgeschlagen: {exc}")
