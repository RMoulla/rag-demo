import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pdfplumber
from flask import Flask, jsonify, render_template, request
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_FILE = DATA_DIR / "document_content.json"


def load_env_file(env_path: Path) -> None:
    """Load key=value pairs from a .env file into process environment."""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value

# Load environment variables from .env when present.
load_env_file(BASE_DIR / ".env")


# Ensure runtime directories/files exist.
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
if not DATA_FILE.exists():
    DATA_FILE.write_text("[]", encoding="utf-8")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20MB upload cap


def extract_pdf_to_records(pdf_path: Path, original_filename: str) -> List[Dict[str, Any]]:
    """Extract page-wise text from a PDF and return JSON-serializable records."""
    records: List[Dict[str, Any]] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            records.append(
                {
                    "file_name": original_filename,
                    "page": index,
                    "text": text.strip(),
                }
            )

    return records


def save_records(records: List[Dict[str, Any]]) -> None:
    """Persist extracted PDF records to the configured JSON file."""
    DATA_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_records() -> List[Dict[str, Any]]:
    """Load extracted PDF records from disk."""
    if not DATA_FILE.exists():
        return []

    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def build_document_context(records: List[Dict[str, Any]]) -> str:
    """Build a prompt-friendly document context that keeps page metadata."""
    parts: List[str] = []
    for item in records:
        file_name = item.get("file_name", "unknown")
        page = item.get("page", "?")
        text = item.get("text", "")
        parts.append(f"[File: {file_name} | Page: {page}]\n{text}")
    return "\n\n".join(parts)


def call_llm(question: str, document_text: str) -> str:
    """Send the question and document context to the OpenAI Chat API."""
    client = OpenAI()

    prompt = (
        "Tu es un assistant spécialisé dans l'extraction d'informations à partir d'un texte. Tu ne dois répondre que si l'information recherchée est contenue dans le contexte fourni\n\n"
        f"Document content:\n{document_text}\n\n"
        f"User question:\n{question}\n\n"
        "Answer the question and also indicate which page(s) contain the relevant information."
    )

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "You answer strictly based on the provided document."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content or ""


def parse_sources_from_answer(answer: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Try to infer page references from the LLM answer text."""
    pages = sorted({int(match) for match in re.findall(r"(?:page|pages?)\s*(\d+)", answer, flags=re.IGNORECASE)})
    if not pages:
        return []

    # Keep only pages that exist in current records and attach file name metadata.
    page_to_file: Dict[int, str] = {}
    for item in records:
        p = item.get("page")
        if isinstance(p, int) and p not in page_to_file:
            page_to_file[p] = item.get("file_name", "unknown")

    sources: List[Dict[str, Any]] = []
    for page in pages:
        if page in page_to_file:
            sources.append({"file_name": page_to_file[page], "page": page})

    return sources


def fallback_sources(records: List[Dict[str, Any]], max_items: int = 3) -> List[Dict[str, Any]]:
    """Fallback source list when answer parsing does not identify page numbers."""
    sources: List[Dict[str, Any]] = []
    seen: set[Tuple[str, int]] = set()
    for item in records:
        file_name = item.get("file_name", "unknown")
        page = item.get("page")
        if isinstance(page, int):
            key = (file_name, page)
            if key not in seen:
                seen.add(key)
                sources.append({"file_name": file_name, "page": page})
        if len(sources) >= max_items:
            break
    return sources


@app.route("/", methods=["GET"])
def index() -> str:
    """Render the main interface."""
    records = load_records()
    file_name = records[0]["file_name"] if records else None
    return render_template("index.html", has_document=bool(records), file_name=file_name, page_count=len(records))


@app.route("/upload", methods=["POST"])
def upload_pdf():
    """Upload and parse a PDF, then store the extracted page text as JSON."""
    if "file" not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "No selected file."}), 400

    if not uploaded_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are allowed."}), 400

    safe_name = os.path.basename(uploaded_file.filename)
    save_path = UPLOAD_DIR / safe_name
    uploaded_file.save(save_path)

    try:
        records = extract_pdf_to_records(save_path, safe_name)
        save_records(records)
    except Exception as exc:
        return jsonify({"error": f"Failed to process PDF: {exc}"}), 500

    return jsonify(
        {
            "message": "Documents chargés avec succès.",
            "file_name": safe_name,
            "pages": len(records),
        }
    )


@app.route("/ask", methods=["POST"])
def ask_question():
    """Answer a user question based on the previously extracted document content."""
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()

    if not question:
        return jsonify({"error": "Question is required."}), 400

    records = load_records()
    if not records:
        return jsonify({"error": "No document is loaded. Please upload a PDF first."}), 400

    context = build_document_context(records)

    try:
        answer = call_llm(question, context)
    except Exception as exc:
        return jsonify({"error": f"Failed to get LLM response: {exc}"}), 500

    sources = parse_sources_from_answer(answer, records)
    if not sources:
        sources = fallback_sources(records)

    return jsonify({"answer": answer, "sources": sources})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)