"""Verified TeddyCloud adapter. It deliberately has no RFID methods."""

from pathlib import Path
from urllib.parse import quote, urlparse
import hashlib
import time
import requests


class TeddyCloud:
    def __init__(self, settings, log=lambda stage, message: None):
        parsed = urlparse(settings.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Ungültige TeddyCloud-Adresse")
        self.url = settings.url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = settings.ca_file or settings.verify_tls
        # TeddyCloud deployments can close idle connections during large transfers.
        self.session.headers["Connection"] = "close"
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.close()

    def request(self, method, path, **kwargs):
        timeout = kwargs.pop("timeout", (15, 180))
        for attempt in range(3 if method == "GET" else 1):
            try:
                response = self.session.request(
                    method, self.url + path, timeout=timeout, **kwargs
                )
                response.encoding = "utf-8"
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    try:
                        detail = response.text[:1000].strip()
                    except requests.RequestException:
                        detail = "Serverantwort konnte nicht gelesen werden"
                    response.close()
                    if detail:
                        exc.args = (f"{exc} · Server: {detail}",)
                    raise
                return response
            except (requests.ConnectionError, requests.Timeout):
                if method != "GET" or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def models(self):
        value = self.request("GET", "/api/toniesCustomJson").json()
        if not isinstance(value, list):
            raise ValueError("Unerwartete Custom-Tonie-Antwort")
        return value

    def index(self, special, folder):
        return self.request(
            "GET", "/api/fileIndexV2", params={"special": special, "path": folder}
        ).json()["files"]

    def ensure_folder(self, relative):
        special, *parts = relative.split("/")
        current = ""
        for part in parts:
            entries = self.index(special, current)
            if not any(x.get("name") == part for x in entries):
                target = "/".join(filter(None, [current, part]))
                try:
                    self.request(
                        "POST",
                        "/api/dirCreate",
                        params={"special": special},
                        data="/" + target,
                        headers={"Content-Type": "text/plain"},
                    )
                except (requests.ConnectionError, requests.Timeout):
                    # An ambiguous POST is resolved by a read, never a blind resend.
                    if not any(
                        x.get("name") == part for x in self.index(special, current)
                    ):
                        raise
                if not any(x.get("name") == part for x in self.index(special, current)):
                    raise ValueError("Serverordner konnte nicht bestätigt werden")
            current = "/".join(filter(None, [current, part]))

    def remote_hash(self, path):
        # Retry the whole GET if a connection breaks after response headers arrived.
        for attempt in range(3):
            digest = hashlib.sha256()
            try:
                with self.request(
                    "GET", "/" + quote(path, safe="/"), stream=True
                ) as response:
                    for chunk in response.iter_content(1024 * 1024):
                        digest.update(chunk)
                return digest.hexdigest()
            except requests.HTTPError as exc:
                if exc.response.status_code == 404:
                    return None
                raise
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ):
                if attempt == 2:
                    raise
                self.log(
                    "Verbindung",
                    f"Dateiprüfung nach Abbruch erneut lesen ({attempt+2}/3): {path}",
                )
                time.sleep(attempt + 1)

    def upload_verified(self, local, folder, name, verify_only=False):
        local = Path(local)
        with local.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        remote = folder + "/" + name
        if self.remote_hash(remote) == expected:
            self.log(
                "Bereits geprüft",
                remote + " · vollständige SHA256 stimmt, Upload übersprungen",
            )
            return False
        if verify_only:
            raise ValueError("Serverdatei fehlt oder ist verändert: " + remote)
        self.ensure_folder(folder)
        special, _, target = folder.partition("/")
        for attempt in range(3):
            self.log("Hochladen", f"{remote} · Versuch {attempt+1}/3")
            failure = None
            try:
                with local.open("rb") as stream:
                    response = self.request(
                        "POST",
                        "/api/fileUpload",
                        params={"special": special, "path": target},
                        files={name: (name, stream, "application/octet-stream")},
                        timeout=(
                            15,
                            (
                                180
                                if local.suffix.lower()
                                in {".jpg", ".jpeg", ".png", ".webp"}
                                else 600
                            ),
                        ),
                    )
                if response.text.strip() != "OK":
                    failure = "Server hat den Upload nicht bestätigt"
                response.close()
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ) as exc:
                failure = type(exc).__name__
            except requests.HTTPError as exc:
                if exc.response.status_code < 500:
                    raise
                failure = f"HTTP {exc.response.status_code}"
            # A read must succeed before any resend. Unknown server state stops here.
            self.log(
                "Upload prüfen", remote + " · vollständige SHA256 vom Server lesen"
            )
            try:
                actual = self.remote_hash(remote)
            except (requests.RequestException, ValueError) as exc:
                raise ValueError(
                    f"Serverstatus nach Upload nicht erreichbar: {remote}. Kein weiterer Upload ohne erfolgreichen Bestandscheck. Bitte später wiederholen; vorhandene Dateien werden geprüft."
                ) from exc
            if actual == expected:
                return True
            if attempt == 2:
                raise ValueError(
                    f"Upload nach 3 Versuchen nicht verifiziert: {remote}. {failure or 'Serverdatei fehlt oder stimmt nicht mit Quelle überein'}. Beim Wiederholen werden bereits korrekte Dateien übersprungen."
                )
            self.log(
                "Wiederaufnahme",
                f"{remote} · Serverdatei nachweislich fehlend oder abweichend; erneuter Upload",
            )
            time.sleep(attempt + 1)

    def import_book(
        self,
        book,
        naming,
        taf,
        info,
        cover=None,
        verify_only=False,
        existing="Prüfen und aktualisieren",
    ):
        models = [m for m in self.models() if m.get("model") == naming["model"]]
        if len(models) > 1:
            raise ValueError("Doppelte Modellkennung auf dem Server")
        if models and existing == "Bei vorhandenem Modell stoppen" and not verify_only:
            raise ValueError(
                "Modell besteht bereits; Importregel verhindert Änderungen."
            )
        self.upload_verified(
            taf, naming["audio_folder"], naming["filename"] + ".taf", verify_only
        )
        # Server's interpretation of the header must agree with our independent parser.
        special, _, folder = naming["audio_folder"].partition("/")
        matches = [
            f
            for f in self.index(special, folder)
            if f.get("name") == naming["filename"] + ".taf"
        ]
        if len(matches) != 1:
            raise ValueError("Audioeintrag nicht eindeutig")
        remote_header = matches[0].get("tafHeader", {})
        if (
            not remote_header.get("valid")
            or str(remote_header.get("audioId")) != info["audioId"]
            or remote_header.get("sha1Hash", "").lower() != info["sha1Hash"]
        ):
            raise ValueError("Server bestätigt den TAF-Header nicht")
        picture = ""
        if cover:
            name = naming["filename"] + Path(cover).suffix
            self.upload_verified(cover, naming["image_folder"], name, verify_only)
            picture = "/" + naming["image_folder"] + "/" + name
        entry = {
            "model": naming["model"],
            "title": naming["title"],
            "series": naming["series"],
            "episodes": naming["episodes"],
            "no": str(naming.get("episode") or 0),
            "language": naming["language"],
            "category": naming["category"],
            "pic": picture,
            "audio_id": [info["audioId"]],
            "hash": [info["sha1Hash"]],
            "tracks": [],
        }

        def equal(model):
            return all(
                (
                    [str(v).lower() for v in model.get(key, [])]
                    == [str(v).lower() for v in value]
                    if key in {"hash", "audio_id"}
                    else (
                        str(model.get(key, "")) == value
                        if key == "no"
                        else model.get(key) == value
                    )
                )
                for key, value in entry.items()
            )

        if not models or not equal(models[0]):
            if verify_only:
                raise ValueError("Custom-Tonie-Daten stimmen nicht überein")
            self.log("Modell speichern", entry["model"])
            try:
                self.request(
                    "POST",
                    "/api/toniesCustomJsonUpsert",
                    json={**(models[0] if models else {}), **entry},
                )
            except (requests.Timeout, requests.ConnectionError):
                check = [m for m in self.models() if m.get("model") == entry["model"]]
                if len(check) != 1 or not equal(check[0]):
                    raise
        check = [m for m in self.models() if m.get("model") == entry["model"]]
        if len(check) != 1 or not equal(check[0]):
            raise ValueError("Modell konnte nach dem Speichern nicht bestätigt werden")
        return entry
