import base64
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlparse

from PIL import Image
import zxingcpp


PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#171a1d">
<title>EventTech Scanner</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#171a1d;color:#eef3f8;font:17px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:620px;margin:auto;padding:24px 18px 40px}h1{font-size:26px;margin:12px 0 6px}
p{color:#bac5ce;line-height:1.45}.card{background:#252b30;border-radius:16px;padding:20px;margin-top:18px}
input,button{width:100%;font-size:18px;border-radius:10px;padding:15px;margin-top:12px}
input{background:#111519;border:1px solid #48545e;color:white}button{border:0;background:#1976d2;color:white;font-weight:700}
button:disabled{opacity:.55}#status{min-height:28px;margin-top:14px;font-weight:600}.success{color:#7ee787}.error{color:#ff7b72}
.hint{font-size:14px;color:#aab5bf}.icon{font-size:36px}.guidance{color:#ffd666}
</style>
</head>
<body><main>
<div class="icon">▦</div><h1>EventTech Scanner</h1>
<p>Funktioniert im Browser auf iPad/iPhone und Android. Scans gehen direkt an den gekoppelten Windows-PC.</p>
<section class="card">
<h2>Mit Kamera scannen</h2>
<p class="hint">Barcode mittig halten und fotografieren. Die App wertet das Bild automatisch aus und erfasst erkannte Barcodes sofort. Gute Beleuchtung und ruhige Hände helfen.</p>
<input id="camera" type="file" accept="image/*" capture="environment">
<p class="hint">Wenn der Barcode nicht erkannt wird: näher herangehen, den Code vollständig ins Bild nehmen und erneut fotografieren. Bei einer Abstandsmeldung wird angezeigt, ob Sie näher heran oder etwas zurück sollen.</p>
</section>
<section class="card">
<h2>Barcode manuell eingeben</h2>
<p class="hint">Sie können auch einen Bluetooth-HID-Scanner koppeln oder den Code eintippen.</p>
<form id="form"><input id="code" autocomplete="off" autocapitalize="off" placeholder="Barcode" required>
<button type="submit">Barcode senden</button></form>
</section>
<div id="status" role="status" aria-live="polite"></div>
<p class="hint">Nur im lokalen WLAN verwenden. Die Kopplung gilt, solange der Windows-Manager geöffnet ist.</p>
</main>
<script>
const token=new URLSearchParams(location.search).get("key");
const status=document.getElementById("status"),camera=document.getElementById("camera");
function message(text,kind="success"){status.textContent=text;status.className=kind}
async function sendCode(code,guidance=""){
  const response=await fetch("/api/scan",{method:"POST",headers:{"Content-Type":"application/json","X-EventTech-Token":token},body:JSON.stringify({code})});
  const result=await response.json();if(!response.ok)throw new Error(result.error||"Übertragung fehlgeschlagen");
  message("Automatisch erfasst: "+result.code+(guidance?" · "+guidance:""),"success");document.getElementById("code").value="";
}
document.getElementById("form").addEventListener("submit",async e=>{
  e.preventDefault();const code=document.getElementById("code").value.trim();if(!code)return;
  try{await sendCode(code)}catch(err){message(err.message,"error")}
});
camera.addEventListener("change",async()=>{
  const file=camera.files[0];if(!file)return;message("Bild wird ausgewertet …","guidance");
  try{
    const bitmap=window.createImageBitmap?await createImageBitmap(file):await new Promise((resolve,reject)=>{
      const image=new Image(),url=URL.createObjectURL(file);
      image.onload=()=>{URL.revokeObjectURL(url);resolve(image)};
      image.onerror=()=>{URL.revokeObjectURL(url);reject(new Error("Das Kamerabild konnte nicht geöffnet werden."))};
      image.src=url;
    }),scale=Math.min(1,1800/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement("canvas");canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);
    canvas.getContext("2d").drawImage(bitmap,0,0,canvas.width,canvas.height);if(bitmap.close)bitmap.close();
    const image=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.88));
    const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(",")[1]);reader.onerror=reject;reader.readAsDataURL(image)});
    const response=await fetch("/api/photo",{method:"POST",headers:{"Content-Type":"application/json","X-EventTech-Token":token},body:JSON.stringify({image:encoded})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||"Bild konnte nicht ausgewertet werden");
    if(!result.codes.length){message(result.guidance||"Kein Barcode erkannt. Bitte näher herangehen und erneut fotografieren.","guidance");return}
    for(const item of result.codes)await sendCode(item.code,item.guidance);
  }catch(err){message(err.message,"error")}finally{camera.value=""}
});
</script></body></html>"""


def local_ipv4_addresses() -> list[str]:
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            address = probe.getsockname()[0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


class IpadScannerServer:
    def __init__(self, on_scan, host: str = "0.0.0.0", port: int = 0):
        self.token = secrets.token_urlsafe(24)
        self.on_scan = on_scan
        token = self.token
        callback = on_scan

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _respond(self, status: int, data: dict):
                payload = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def _authorized(self) -> bool:
                return secrets.compare_digest(self.headers.get("X-EventTech-Token", ""), token)

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path != "/" or not secrets.compare_digest(query.get("key", [""])[0], token):
                    self.send_error(404)
                    return
                page = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(page)

            def do_POST(self):
                if not self._authorized():
                    self._respond(403, {"error": "Kopplung ungültig. Bitte neu verbinden."})
                    return
                if self.path not in ("/api/scan", "/api/photo"):
                    self._respond(404, {"error": "Unbekannter Endpunkt."})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 12 * 1024 * 1024:
                        self._respond(413, {"error": "Die Anfrage ist zu groß."})
                        return
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        self._respond(400, {"error": "Anfrageformat ungültig."})
                        return
                    if self.path == "/api/scan":
                        code = str(body.get("code", "")).strip()
                        if not code or len(code) > 256:
                            self._respond(400, {"error": "Barcode fehlt oder ist zu lang."})
                            return
                        callback(code)
                        self._respond(200, {"code": code})
                        return
                    image_bytes = base64.b64decode(body.get("image", ""), validate=True)
                    if len(image_bytes) > 9 * 1024 * 1024:
                        self._respond(413, {"error": "Das Bild ist zu groß."})
                        return
                    with Image.open(BytesIO(image_bytes)) as image:
                        if image.width * image.height > 25_000_000:
                            self._respond(413, {"error": "Die Bildauflösung ist zu hoch."})
                            return
                        image.load()
                        image = image.convert("RGB")
                        results = zxingcpp.read_barcodes(image)
                        image_width, image_height = image.size
                    codes = []
                    seen = set()
                    for result in results:
                        code = result.text.strip()
                        if not code or code in seen:
                            continue
                        seen.add(code)
                        ratio = self._barcode_size_ratio(result, image_width, image_height)
                        if ratio is None:
                            guidance = "Erkannt. Halten Sie das Gerät ruhig und nehmen Sie den Code vollständig auf."
                        elif ratio < 0.18:
                            guidance = "Barcode erkannt, aber sehr klein. Gehen Sie näher heran."
                        elif ratio > 0.82:
                            guidance = "Barcode erkannt, aber sehr groß. Gehen Sie etwas weiter zurück."
                        else:
                            guidance = "Abstand passt. Barcode wird automatisch erfasst."
                        codes.append({"code": code, "guidance": guidance, "size_ratio": ratio})
                    guidance = (
                        "Kein Barcode erkannt. Gehen Sie näher heran, halten Sie die Kamera ruhig und sorgen Sie für gutes Licht."
                        if not codes else ""
                    )
                    self._respond(200, {"codes": codes, "guidance": guidance})
                except (ValueError, json.JSONDecodeError, OSError) as exc:
                    self._respond(400, {"error": f"Bild oder Anfrage ungültig: {exc}"})

            @staticmethod
            def _barcode_size_ratio(result, image_width: int, image_height: int):
                position = result.position
                points = (
                    position.top_left, position.top_right,
                    position.bottom_left, position.bottom_right,
                )
                if image_width <= 0 or image_height <= 0:
                    return None
                width = max(point.x for point in points) - min(point.x for point in points)
                height = max(point.y for point in points) - min(point.y for point in points)
                ratio = max(width / image_width, height / image_height)
                return round(max(0.0, min(1.0, ratio)), 3)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
