import re
import time
import html
from datetime import date

import requests
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)

# Render sits behind a proxy, so read the visitor's real address from it
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Paste your Google Form link between the quotes when you have one.
# Leave it empty and the feedback link will simply not show.
FEEDBACK_URL = ""

GREENBOOK_URL = "https://greenbook.nafdac.gov.ng/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Veripharm AI)",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
}

COLUMNS = [
    "product_name", "ingredient.ingredient_name", "product_category.name",
    "product_category_id", "ingredient.synonym", "NAFDAC", "form.name",
    "route.name", "strength", "applicant.name", "approval_date", "status",
]
NAFDAC_COLUMN = COLUMNS.index("NAFDAC")

CACHE = {}
CACHE_SECONDS = 6 * 60 * 60  # remember answers for 6 hours


def build_params(number):
    params = {"draw": 1, "start": 0, "length": 50}
    for i, name in enumerate(COLUMNS):
        params[f"columns[{i}][data]"] = name
        params[f"columns[{i}][name]"] = name
        params[f"columns[{i}][searchable]"] = "true"
        params[f"columns[{i}][orderable]"] = "false" if name == "product_category.name" else "true"
        params[f"columns[{i}][search][value]"] = number if i == NAFDAC_COLUMN else ""
        params[f"columns[{i}][search][regex]"] = "false"
    params["order[0][column]"] = 0
    params["order[0][dir]"] = "asc"
    return params


def fetch_with_retry(params, tries=3):
    for attempt in range(1, tries + 1):
        try:
            return requests.get(GREENBOOK_URL, params=params, headers=HEADERS, timeout=30)
        except requests.exceptions.RequestException:
            time.sleep(2 * attempt)
    return None


def clean_number(text):
    return text.strip().upper().replace(" ", "")


def look_up(number):
    number = clean_number(number)

    if not re.fullmatch(r"[A-Z0-9/\-]{3,25}", number):
        return {"status": "invalid",
                "message": "That does not look like a NAFDAC number. Example: A4-6238"}

    response = fetch_with_retry(build_params(number))
    if response is None or response.status_code != 200:
        return {"status": "error",
                "message": "Could not reach the Greenbook right now. Please try again later."}

    try:
        records = response.json().get("data", [])
    except ValueError:
        return {"status": "error",
                "message": "The Greenbook sent an unexpected reply. Please try again later."}

    matches = [r for r in records if clean_number(r.get("NAFDAC") or "") == number]

    if not matches:
        return {"status": "not_found", "number": number,
                "message": "No match found in the Greenbook. This does not prove the "
                           "product is fake: the number may be mistyped, or the product may "
                           "be a food or cosmetic listed on NAFDAC's other system (NAPAMS). "
                           "If you are unsure, report it to NAFDAC."}

    results = []
    for r in matches:
        expiry = r.get("expiry_date")
        days_left = None
        if expiry:
            try:
                days_left = (date.fromisoformat(expiry) - date.today()).days
            except ValueError:
                pass
        results.append({
            "product": (r.get("product_name") or "").rstrip("#*$ "),
            "ingredient": r.get("ingredient_name"),
            "strength": r.get("strength"),
            "form": r.get("form_name"),
            "applicant": r.get("applicant_name"),
            "pack_size": html.unescape(r.get("pack_size") or ""),
            "status": r.get("status"),
            "expiry": expiry,
            "days_left": days_left,
        })

    return {"status": "found", "number": number, "records": results}


@app.errorhandler(429)
def too_many(e):
    return jsonify({"status": "error",
                    "message": "Too many checks from your connection. Please wait a while and try again."}), 429


@app.route("/check")
@limiter.limit("60 per hour")
def check():
    number = clean_number(request.args.get("number", ""))
    now = time.time()

    cached = CACHE.get(number)
    if cached and now - cached[0] < CACHE_SECONDS:
        return jsonify(cached[1])

    result = look_up(number)

    # Only remember real answers, never errors
    if result["status"] in ("found", "not_found"):
        if len(CACHE) > 2000:
            CACHE.clear()
        CACHE[number] = (now, result)

    return jsonify(result)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veripharm AI</title>
<style>
  body { font-family: Arial, sans-serif; background: #f2f7f3; margin: 0; color: #1c2b21; }
  header { background: #1f7a4a; color: white; padding: 24px 16px; text-align: center; }
  header h1 { margin: 0 0 6px; font-size: 26px; }
  header p { margin: 0; opacity: 0.9; }
  main { max-width: 640px; margin: 0 auto; padding: 20px 16px 40px; }
  .box { display: flex; gap: 8px; }
  input { flex: 1; padding: 14px; font-size: 18px; border: 2px solid #b8d4c2; border-radius: 8px; }
  button { padding: 14px 20px; font-size: 18px; background: #1f7a4a; color: white;
           border: none; border-radius: 8px; cursor: pointer; }
  button:disabled { background: #7aa88d; }
  .card { background: white; border-radius: 10px; padding: 16px; margin-top: 16px;
          border-left: 6px solid #1f7a4a; box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
  .card.warn { border-left-color: #d98e04; }
  .card.bad { border-left-color: #c0392b; }
  .card h2 { margin: 0 0 10px; font-size: 20px; }
  .row { display: flex; padding: 5px 0; border-bottom: 1px solid #eef2ef; }
  .row span:first-child { width: 150px; color: #5b6b61; flex-shrink: 0; }
  .flag { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 14px;
          margin-left: 6px; color: white; background: #d98e04; }
  .flag.red { background: #c0392b; }
  .note { margin-top: 16px; padding: 12px; background: #fff8e1; border-radius: 8px;
          font-size: 15px; line-height: 1.4; }
  footer { text-align: center; font-size: 13px; color: #5b6b61; padding: 0 16px 30px; line-height: 1.6; }
  footer a { color: #1f7a4a; font-weight: bold; }
</style>
</head>
<body>
<header>
  <h1>Veripharm AI</h1>
  <p>Check a medicine's NAFDAC registration number</p>
</header>
<main>
  <div class="box">
    <input id="number" type="text" placeholder="e.g. A4-6238" autocomplete="off">
    <button id="btn" onclick="check()">Check</button>
  </div>
  <div id="result"></div>
</main>
<footer>
  This tool checks the NAFDAC Greenbook (medicines only for now). It is not an official NAFDAC service.<br>
  Food and cosmetics will not appear here. If unsure about a product, report it to NAFDAC.
  __FEEDBACK__
</footer>

<script>
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function row(card, label, value) {
  const r = el("div", "row");
  r.appendChild(el("span", "", label));
  r.appendChild(el("span", "", value || "-"));
  card.appendChild(r);
  return r;
}

function showMessage(kind, title, text) {
  const out = document.getElementById("result");
  out.innerHTML = "";
  const card = el("div", "card " + kind);
  card.appendChild(el("h2", "", title));
  card.appendChild(el("div", "", text));
  out.appendChild(card);
}

function render(data) {
  const out = document.getElementById("result");
  out.innerHTML = "";

  if (data.status !== "found") {
    const kind = data.status === "not_found" ? "bad" : "warn";
    const title = data.status === "not_found" ? "No match found" : "Problem";
    showMessage(kind, title, data.message);
    return;
  }

  data.records.forEach(function (rec) {
    const card = el("div", "card");
    card.appendChild(el("h2", "", "Registered: " + rec.product));
    row(card, "NAFDAC number", data.number);
    row(card, "Ingredient", rec.ingredient);
    row(card, "Strength", rec.strength);
    row(card, "Form", rec.form);
    row(card, "Applicant", rec.applicant);
    row(card, "Pack size", rec.pack_size);
    row(card, "Status", rec.status);
    const exp = row(card, "Registration expires", rec.expiry);
    if (rec.days_left !== null) {
      if (rec.days_left < 0) {
        card.className = "card bad";
        exp.lastChild.appendChild(el("span", "flag red", "EXPIRED"));
      } else if (rec.days_left <= 90) {
        card.className = "card warn";
        exp.lastChild.appendChild(el("span", "flag", "Expires in " + rec.days_left + " days"));
      }
    }
    out.appendChild(card);
  });

  out.appendChild(el("div", "note",
    "Important: compare this with the pack in your hand: product name, company, strength, " +
    "pack size, batch number and the expiry date printed on the pack. A registered number " +
    "does not prove the pack is genuine, because counterfeiters can copy real numbers."));
}

async function check() {
  const number = document.getElementById("number").value.trim();
  if (!number) return;
  const btn = document.getElementById("btn");
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    const res = await fetch("/check?number=" + encodeURIComponent(number));
    render(await res.json());
  } catch (e) {
    showMessage("warn", "Problem", "Could not reach the checker. Please try again.");
  }
  btn.disabled = false;
  btn.textContent = "Check";
}

document.getElementById("number").addEventListener("keydown", function (e) {
  if (e.key === "Enter") check();
});
</script>
</body>
</html>"""


@app.route("/")
def home():
    if FEEDBACK_URL:
        feedback = ('<br><a href="' + FEEDBACK_URL +
                    '" target="_blank" rel="noopener">Give feedback</a>')
    else:
        feedback = ""
    return PAGE.replace("__FEEDBACK__", feedback)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)