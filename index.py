# index.py
#
# FastAPI app to compare two lists of Pokémon.
# - Accepts two inputs: an "Owned" list and a "List to Check".
# - Each input can be a file upload (.txt) or pasted text.
# - Parses lists where each Pokémon is on a new line.
# - Compares lists case-insensitively but preserves original casing from the "List to Check".
# - Outputs the count and names of Pokémon from the second list that are not in the first.
#
# Recommended Render start command:
#   gunicorn -w 4 -k uvicorn.workers.UvicornWorker index:app

import os
import re
import sys
import traceback
import logging
from html import escape
from typing import List, Optional, Set

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

# --- logging ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("pokemon-comparison-tool")

app = FastAPI(title="Pokémon Comparison Tool")

# --- HTML UI ---
# CSS curly braces {} are escaped by doubling them to {{}} for Python's .format()
HTML_UI = """<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Pokémon Comparison Tool</title>
    <style>
      body {{ font-family: Inter, Arial, sans-serif; margin: 22px; background: #f6f7fb; color: #111; }}
      .container {{ max-width: 1200px; margin: 0 auto; }}
      .card {{ background: #fff; padding: 14px 22px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); margin-bottom: 20px; }}
      h1, h2 {{ margin-top: 0; }}
      pre {{ white-space: pre-wrap; font-family: monospace; background: #f8f9fb; padding: 10px; border-radius: 6px; border: 1px solid #e1e4e8; }}
      textarea {{ width: 100%; box-sizing: border-box; height: 160px; font-family: monospace; padding: 8px; border-radius: 6px; border: 1px solid #ccc; }}
      .form-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
      .input-group label {{ display: block; font-weight: bold; margin-bottom: 8px; }}
      .input-group input[type="file"] {{ margin-bottom: 12px; }}
      .submit-area {{ text-align: center; margin-top: 20px; }}
      button {{ font-size: 16px; padding: 10px 20px; border-radius: 6px; border: 1px solid #ccc; cursor: pointer; background-color: #007bff; color: white; border-color: #007bff; }}
      @media (max-width: 768px) {{ .form-grid {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
<div class="container">
    <h1>Pokémon Comparison Tool</h1>
    <form method="post" enctype="multipart/form-data">
        <div class="card form-grid">
            <div class="input-group">
                <h2>1. Owned Pokémon</h2>
                <label for="owned_file">Upload owned.txt</label>
                <input type="file" name="owned_file" id="owned_file" accept=".txt">
                <label for="owned_text">Or paste list here:</label>
                <textarea name="owned_text" id="owned_text" placeholder="One Pokémon name per line..."></textarea>
            </div>
            <div class="input-group">
                <h2>2. List to Check</h2>
                <label for="checklist_file">Upload list.txt</label>
                <input type="file" name="checklist_file" id="checklist_file" accept=".txt">
                <label for="checklist_text">Or paste list here:</label>
                <textarea name="checklist_text" id="checklist_text" placeholder="One Pokémon name per line..."></textarea>
            </div>
        </div>
        <div class="submit-area">
            <button type="submit">Compare Lists</button>
        </div>
    </form>
    <div class="card">
        <h2>Result:</h2>
        <pre>{result_block}</pre>
    </div>
</div>
</body>
</html>
"""

# --- Core Logic ---

def parse_pokemon_list(text: str) -> List[str]:
    """
    Parses a string containing one Pokémon per line into a clean list.
    - Splits by newline.
    - Trims whitespace from each line.
    - Ignores empty lines.
    - Returns a list of cleaned names.
    """
    if not text:
        return []
    
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return names

# --- Middleware for Error Handling ---
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Unhandled exception:\n%s", tb)
        if os.environ.get("DEBUG") == "1" or request.query_params.get("debug") == "1":
            return PlainTextResponse(tb, status_code=500)
        return PlainTextResponse("Internal Server Error", status_code=500)

# --- Helper to get content from file or text ---
async def get_content(file: Optional[UploadFile], text: Optional[str]) -> str:
    """Prioritizes file content, falls back to text content."""
    content = ""
    if file and file.filename:
        try:
            b = await file.read()
            content = b.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Failed to read file {file.filename}: {e}")
            content = text or ""
    else:
        content = text or ""
    return content

# --- API Routes ---
@app.get("/", response_class=HTMLResponse)
async def form():
    return HTML_UI.format(result_block="Provide both lists and click 'Compare Lists' to see the results.")

@app.post("/", response_class=HTMLResponse)
async def compare_lists(
    owned_file: Optional[UploadFile] = File(None),
    owned_text: Optional[str] = Form(None),
    checklist_file: Optional[UploadFile] = File(None),
    checklist_text: Optional[str] = Form(None)
):
    # Get content for both lists
    owned_content = await get_content(owned_file, owned_text)
    checklist_content = await get_content(checklist_file, checklist_text)

    if not owned_content or not checklist_content:
        result_block = "Error: Please provide content for both the 'Owned' list and the 'List to Check'."
        return HTML_UI.format(result_block=escape(result_block))

    # Parse the lists
    owned_names = parse_pokemon_list(owned_content)
    checklist_names = parse_pokemon_list(checklist_content)

    # For efficient, case-insensitive lookup, create a set of lowercased owned names
    owned_names_lower_set: Set[str] = {name.lower() for name in owned_names}

    # Find which Pokémon from the checklist are missing from the owned list
    missing_pokemon = []
    seen_missing_lower = set() # To avoid adding duplicates from the checklist itself
    
    for name in checklist_names:
        name_lower = name.lower()
        if name_lower not in owned_names_lower_set and name_lower not in seen_missing_lower:
            missing_pokemon.append(name) # Preserve original casing from checklist
            seen_missing_lower.add(name_lower)
            
    # Sort the results alphabetically
    missing_pokemon.sort(key=str.lower)

    # Build the final output string
    lines = []
    count = len(missing_pokemon)
    if count > 0:
        lines.append(f"You are missing {count} Pokémon from the list:")
        lines.append("") # Blank line
        lines.extend(missing_pokemon)
    else:
        lines.append("Congratulations! You own every Pokémon from the list.")

    result_block = escape("\n".join(lines))
    return HTML_UI.format(result_block=result_block)

# --- Local Server Runner ---
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("DEBUG") == "1"
    uvicorn.run("index:app", host="0.0.0.0", port=port, reload=reload)

