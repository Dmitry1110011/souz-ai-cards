"""Простая веб-форма и JSON API. Запуск: uvicorn app.web:app --reload"""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import load_settings
from .errors import CardError, ConfigError, InputError
from .generator import generate_card
from .llm import OpenAICompatClient
from .render import to_markdown
from .validation import parse_product

app = FastAPI(title="Черновик карточки товара")

EXAMPLE = """{
  "name": "Манометр показывающий DM 02",
  "manufacturer": "Манотомь",
  "specs": {
    "диапазон измерений": "0–1,6 МПа",
    "класс точности": "1,5",
    "присоединение": "G1/2",
    "диаметр корпуса": "100 мм"
  },
  "description": "Прибор предназначен для измерения избыточного давления..."
}"""

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Черновик карточки товара</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1b1f23}
textarea{width:100%;height:240px;font:13px/1.4 ui-monospace,monospace;padding:8px;box-sizing:border-box}
button{padding:8px 16px;margin:8px 8px 8px 0;cursor:pointer}
pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px}
.err{color:#b42318;font-weight:600}
</style></head><body>
<h1>Черновик карточки товара</h1>
<p>Вставьте JSON с данными о приборе (name, manufacturer, specs, description).</p>
<textarea id="inp">__EXAMPLE__</textarea><br>
<button id="go">Сформировать</button><button id="copy" hidden>Копировать Markdown</button>
<span id="status"></span>
<div id="err" class="err"></div><pre id="out" hidden></pre>
<script>
const $=id=>document.getElementById(id);
$("go").onclick=async()=>{
  $("err").textContent="";$("out").hidden=true;$("copy").hidden=true;$("status").textContent="Генерирую…";$("go").disabled=true;
  try{
    const r=await fetch("/api/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({raw:$("inp").value})});
    const d=await r.json();
    if(!r.ok){$("err").textContent=d.error||"Ошибка";}
    else{$("out").textContent=d.markdown;$("out").hidden=false;$("copy").hidden=false;}
  }catch(e){$("err").textContent="Не удалось связаться с сервером: "+e;}
  $("status").textContent="";$("go").disabled=false;
};
$("copy").onclick=()=>navigator.clipboard.writeText($("out").textContent);
</script></body></html>"""


@lru_cache(maxsize=1)
def _settings():
    return load_settings()


@lru_cache(maxsize=1)
def _llm() -> OpenAICompatClient:
    return OpenAICompatClient(_settings())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE.replace("__EXAMPLE__", EXAMPLE.replace("&", "&amp;").replace("<", "&lt;"))


@app.post("/api/generate")
async def api_generate(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "Тело запроса должно быть JSON."}, status_code=400)

    # Принимаем и {"raw": "<строка JSON>"} (форма), и сам товар как объект (API).
    payload = body["raw"] if isinstance(body, dict) and set(body) == {"raw"} else body
    try:
        product = parse_product(payload)
        card = generate_card(product, _llm(), verify=_settings().verify)
    except InputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    except CardError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

    return JSONResponse({"card": card.model_dump(), "markdown": to_markdown(card)})
