import base64
from datetime import datetime, timedelta, timezone
import html
import ipaddress
import json
import secrets
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from modules.secret_store import protect_secret, unprotect_secret
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
video{display:block;width:100%;max-height:58vh;min-height:220px;background:#090b0d;border-radius:12px;object-fit:cover}
.camera-actions{display:flex;gap:10px}.camera-actions button{flex:1}
</style>
</head>
<body><main>
<div class="icon">▦</div><h1>EventTech Scanner</h1>
<p>Live-Kamera auf iPad/iPhone und Android. Erkannte Barcodes werden direkt an den gekoppelten Lager-Manager gesendet.</p>
<section class="card">
<h2>Live-Kamera</h2>
<video id="preview" autoplay muted playsinline aria-label="Live-Kamerabild"></video>
<div class="camera-actions">
<button id="startCamera" type="button">Live-Kamera starten</button>
<button id="stopCamera" type="button" disabled>Kamera stoppen</button>
</div>
<p class="hint">Barcode mittig ins Bild halten. Erkannte Codes werden automatisch erfasst. Gute Beleuchtung und ruhige Hände helfen.</p>
<p class="hint">Für Live-Video muss das Zertifikat dieses PCs einmalig auf dem Mobilgerät als vertrauenswürdig installiert werden. Laden Sie es hier herunter: <a href="__CERTIFICATE_URL__">Kamera-Zertifikat installieren</a>.</p>
<p><a href="__SECURE_URL__">Live-Scanner mit HTTPS öffnen</a></p>
<details>
<summary>Einzelnes Foto aufnehmen</summary>
<input id="camera" type="file" accept="image/*" capture="environment">
<p class="hint">Wenn der Barcode nicht erkannt wird, gehen Sie näher heran und nehmen Sie den Code vollständig auf.</p>
</details>
</section>
<section class="card">
<h2>Barcode manuell eingeben</h2>
<p class="hint">Sie können auch einen Bluetooth-HID-Scanner koppeln oder den Code eintippen.</p>
<form id="form"><input id="code" autocomplete="off" autocapitalize="off" placeholder="Barcode" required>
<button type="submit">Barcode senden</button></form>
</section>
<div id="status" role="status" aria-live="polite"></div>
<p class="hint">Nur im lokalen WLAN verwenden. Die Kopplung gilt, solange der Lager-Manager geöffnet ist.</p>
</main>
<script>
const token=new URLSearchParams(location.search).get("key");
const status=document.getElementById("status"),camera=document.getElementById("camera"),preview=document.getElementById("preview");
const startCamera=document.getElementById("startCamera"),stopCamera=document.getElementById("stopCamera");
let cameraStream=null,frameTimer=null,frameBusy=false,lastGuidanceAt=0;
const recentlySeen=new Map();
function message(text,kind="success"){status.textContent=text;status.className=kind}
async function sendCode(code,guidance=""){
  const response=await fetch("/api/scan",{method:"POST",headers:{"Content-Type":"application/json","X-EventTech-Token":token},body:JSON.stringify({code})});
  const result=await response.json();if(!response.ok)throw new Error(result.error||"Übertragung fehlgeschlagen");
  message("Automatisch erfasst: "+result.code+(guidance?" · "+guidance:""),"success");document.getElementById("code").value="";
}
async function scanFrame(){
  if(!cameraStream||frameBusy)return;
  frameBusy=true;
  try{
    const canvas=document.createElement("canvas"),width=Math.min(960,preview.videoWidth);
    if(!width||!preview.videoHeight)return;
    canvas.width=width;canvas.height=Math.round(preview.videoHeight*width/preview.videoWidth);
    canvas.getContext("2d").drawImage(preview,0,0,canvas.width,canvas.height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.72));
    if(!blob)return;
    const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(",")[1]);reader.onerror=reject;reader.readAsDataURL(blob)});
    const response=await fetch("/api/photo",{method:"POST",headers:{"Content-Type":"application/json","X-EventTech-Token":token},body:JSON.stringify({image:encoded})});
    const result=await response.json();if(!response.ok)throw new Error(result.error||"Kamerabild konnte nicht ausgewertet werden");
    if(result.codes.length){
      const now=Date.now(),visible=new Set(result.codes.map(item=>item.code));
      for(const item of result.codes){
        const lastSeen=recentlySeen.get(item.code);
        recentlySeen.set(item.code,now);
        if(lastSeen===undefined||now-lastSeen>1800)await sendCode(item.code,item.guidance)
      }
      for(const [code,lastSeen] of recentlySeen)if(now-lastSeen>1800&&!visible.has(code))recentlySeen.delete(code)
    }
    else if(Date.now()-lastGuidanceAt>4000){message(result.guidance||"Kamera bereit. Barcode mittig ins Bild halten.","guidance");lastGuidanceAt=Date.now()}
  }catch(err){message(err.message,"error")}
  finally{frameBusy=false;if(cameraStream)frameTimer=setTimeout(scanFrame,350)}
}
startCamera.addEventListener("click",async()=>{
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    message("Live-Kamera wird blockiert: Dieser Browser benötigt HTTPS. Verwenden Sie die Fotoaufnahme oder öffnen Sie die Seite über eine sichere Verbindung.","error");return
  }
  try{
    cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"},width:{ideal:1280},height:{ideal:720}},audio:false});
    preview.srcObject=cameraStream;await preview.play();
    startCamera.disabled=true;stopCamera.disabled=false;message("Live-Kamera aktiv. Barcode mittig ins Bild halten.","guidance");scanFrame()
  }catch(err){
    cameraStream=null;
    const reason=err.name==="NotAllowedError"?"Kamerazugriff abgelehnt. Erlauben Sie den Kamerazugriff in den Browser-Einstellungen.":err.name==="NotFoundError"?"Keine Kamera auf diesem Gerät gefunden.":err.message;
    message("Live-Kamera konnte nicht gestartet werden: "+reason,"error")
  }
});
function stopLiveCamera(){
  if(frameTimer)clearTimeout(frameTimer);frameTimer=null;
  if(cameraStream)cameraStream.getTracks().forEach(track=>track.stop());
  cameraStream=null;preview.srcObject=null;startCamera.disabled=false;stopCamera.disabled=true;
}
stopCamera.addEventListener("click",stopLiveCamera);
window.addEventListener("pagehide",stopLiveCamera);
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
    def __init__(
        self,
        on_scan,
        host: str = "0.0.0.0",
        port: int = 0,
        certificate_dir: Path | None = None,
        certificate_hosts: list[str] | None = None,
    ):
        self.token = secrets.token_urlsafe(24)
        self.on_scan = on_scan
        token = self.token
        callback = on_scan
        self.certificate_dir = Path(certificate_dir or Path.home() / ".eventtech-scanner-certificate")
        self.certificate_dir.mkdir(parents=True, exist_ok=True)
        hosts = certificate_hosts or ["127.0.0.1"]
        public_host = hosts[0]
        ca_path, server_cert_path, server_key_path = self._ensure_certificate(hosts)
        ca_certificate = ca_path.read_bytes()

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
                authorized = secrets.compare_digest(query.get("key", [""])[0], token)
                if not authorized:
                    self.send_error(404)
                    return
                if parsed.path == "/certificate":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-x509-ca-cert")
                    self.send_header("Content-Disposition", 'attachment; filename="eventtech-scanner-ca.crt"')
                    self.send_header("Content-Length", str(len(ca_certificate)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(ca_certificate)
                    return
                if parsed.path != "/":
                    self.send_error(404)
                    return
                secure_url = f"https://{public_host}:{https_port}/?key={token}"
                certificate_url = f"http://{public_host}:{http_port}/certificate?key={token}"
                page = PAGE.replace("__SECURE_URL__", html.escape(secure_url, quote=True))
                page = page.replace("__CERTIFICATE_URL__", html.escape(certificate_url, quote=True))
                page = page.encode("utf-8")
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
        http_port = int(self.server.server_address[1])
        self.https_server = ThreadingHTTPServer((host, 0), Handler)
        self.https_server.daemon_threads = True
        https_port = int(self.https_server.server_address[1])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(server_cert_path), str(server_key_path))
        self.https_server.socket = context.wrap_socket(self.https_server.socket, server_side=True)
        self.threads = [
            threading.Thread(target=self.server.serve_forever, daemon=True),
            threading.Thread(target=self.https_server.serve_forever, daemon=True),
        ]

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def https_port(self) -> int:
        return int(self.https_server.server_address[1])

    def start(self):
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.https_server.shutdown()
        self.https_server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def _ensure_certificate(self, hosts: list[str]) -> tuple[Path, Path, Path]:
        ca_key_path = self.certificate_dir / "ca-key.pem"
        ca_cert_path = self.certificate_dir / "ca-cert.pem"
        server_key_path = self.certificate_dir / "server-key.pem"
        server_cert_path = self.certificate_dir / "server-cert.pem"
        ip_addresses = sorted({ipaddress.ip_address(host) for host in hosts}, key=str)
        required_addresses = {str(address) for address in ip_addresses}
        now = datetime.now(timezone.utc)

        try:
            ca_key = serialization.load_pem_private_key(
                unprotect_secret(ca_key_path.read_text(encoding="ascii")).encode("ascii"),
                password=None,
            )
            ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
            server_key = serialization.load_pem_private_key(server_key_path.read_bytes(), password=None)
            server_cert = x509.load_pem_x509_certificate(server_cert_path.read_bytes())
            names = {
                str(name.value)
                for name in server_cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
                if isinstance(name, x509.IPAddress)
            }
            ca_constraints = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            server_constraints = server_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
            server_cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)
            if (
                required_addresses.issubset(names)
                and ca_constraints.ca
                and not server_constraints.ca
                and server_cert.issuer == ca_cert.subject
                and ca_cert.not_valid_after_utc > now + timedelta(days=365)
                and server_cert.not_valid_after_utc > now + timedelta(days=30)
            ):
                return ca_cert_path, server_cert_path, server_key_path
        except (OSError, ValueError, TypeError, x509.ExtensionNotFound):
            pass

        ca_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "EventTech Local Scanner CA"),
        ])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True,
                    crl_sign=True, encipher_only=None, decipher_only=None,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        server_key = ec.generate_private_key(ec.SECP256R1())
        server_subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "EventTech Local Scanner"),
        ])
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(server_subject)
            .issuer_name(ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=397))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(address) for address in ip_addresses]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        ca_key_pem = ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        ca_key_path.write_text(protect_secret(ca_key_pem), encoding="ascii")
        ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        server_key_path.write_bytes(server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        server_cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
        return ca_cert_path, server_cert_path, server_key_path
