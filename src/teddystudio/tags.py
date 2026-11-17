"""Explicit Tonie/tag assignment, separate from model creation and file transfers."""

import re
import time
from pathlib import PurePosixPath
from .client import TeddyCloud
from .storage import Settings, atomic_json

FIELDS = {
    "source": "",
    "tonie_model": "",
    "live": False,
    "nocloud": False,
    "hide": False,
}


def normalize_uid(value, reverse=False):
    value = re.sub(r"[\s:-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        raise ValueError("UID/RUID muss genau 16 Hex-Zeichen enthalten")
    return bytes.fromhex(value)[::-1].hex() if reverse else value


def overlay_params(overlay):
    if overlay and not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", overlay):
        raise ValueError("Ungültiges Overlay")
    return {"overlay": overlay} if overlay else {}


def read_tag(client, uid, overlay=""):
    import requests

    uid = normalize_uid(uid)
    try:
        raw = client.request(
            "GET",
            "/content/json/get/" + uid,
            params=overlay_params(overlay),
            timeout=(5, 30),
        ).json()
        if not isinstance(raw, dict):
            raise ValueError("Unerwartete Tag-Antwort")
        values = {key: raw.get(key, default) for key, default in FIELDS.items()}
        if any(type(values[k]) is not type(default) for k, default in FIELDS.items()):
            raise ValueError(
                "Tagkonfiguration enthält unerwartete Feldtypen; keine automatische Änderung"
            )
        return {"exists": True, "values": values}
    except requests.HTTPError as exc:
        if exc.response.status_code == 404:
            return {"exists": False, "values": dict(FIELDS)}
        raise


def assign_tag(payload, work, log):
    from .jobs import server_access

    settings = Settings(**payload["settings"])
    options = payload["tag"]
    uid = normalize_uid(options["uid"])
    overlay = options.get("overlay", "")
    params = overlay_params(overlay)
    before = options["before"]
    after = {k: options["after"][k] for k in FIELDS}
    for key in ("source", "tonie_model"):
        if not isinstance(after[key], str) or len(after[key].encode("utf-8")) > 255:
            raise ValueError("Tagwert ist zu lang oder ungültig: " + key)
    for key in ("live", "nocloud", "hide"):
        if type(after[key]) is not bool:
            raise ValueError("Ungültiger Schalter: " + key)
    with server_access(settings.url, log), TeddyCloud(settings, log) as client:
        current = read_tag(client, uid, overlay)
        if current["exists"] and current["values"] == after:
            return {
                "status": "Bestätigt",
                "message": "Tonie-/Tag-Zuordnung bereits korrekt; zurückgelesen",
            }
        if current != before:
            raise ValueError(
                "Tag wurde seit der Vorschau verändert. Neu laden und Änderungen erneut prüfen."
            )
        changed = {
            key: value for key, value in after.items() if value != before["values"][key]
        }
        if not changed:
            return {
                "status": "Bestätigt",
                "message": "Keine Zuordnungsänderung erforderlich",
            }
        source = after["source"]
        if (
            "source" in changed
            and source
            and not source.startswith(("lib://", "content://"))
        ):
            raise ValueError(
                "Neue Quelle muss eine vorhandene lib://- oder content://-Datei sein"
            )
        if ("source" in changed or "tonie_model" in changed) and source.startswith(
            ("lib://", "content://")
        ):
            prefix, path = source.split("://", 1)
            parts = PurePosixPath(path).parts
            if (
                not parts
                or any(p in {"..", "."} for p in parts)
                or path.startswith("/")
                or "\\" in path
            ):
                raise ValueError("Ungültiger Bibliothekspfad")
            check = {"path": "/".join(parts[:-1]), **params}
            if prefix == "lib":
                check["special"] = "library"
            entries = client.request("GET", "/api/fileIndexV2", params=check).json()[
                "files"
            ]
            matches = [
                e
                for e in entries
                if e.get("name") == parts[-1] and e.get("tafHeader", {}).get("valid")
            ]
            if len(matches) != 1:
                raise ValueError("Ziel-Audiodatei fehlt oder TAF-Header ist ungültig")
            if after["tonie_model"]:
                models = client.models()
                models += client.request("GET", "/api/toniesJson").json()
                selected = [
                    m
                    for m in models
                    if str(m.get("model", "")).casefold()
                    == after["tonie_model"].casefold()
                ]
                header = matches[0]["tafHeader"]
                from .cloud_library import model_pairs, pair

                if not any(pair(header) in model_pairs(model) for model in selected):
                    raise ValueError("Modell und Audiodatei passen nicht zusammen")
        elif "tonie_model" in changed and after["tonie_model"]:
            models = client.models() + client.request("GET", "/api/toniesJson").json()
            if not any(
                str(m.get("model", "")).casefold() == after["tonie_model"].casefold()
                for m in models
            ):
                raise ValueError("Modellkennung ist auf diesem Server nicht bekannt")
        atomic_json(
            work / "tag-change.json",
            {
                "url": client.url,
                "uid": uid,
                "overlay": overlay,
                "before": before,
                "after": after,
                "time": time.time(),
            },
        )
        # Only editable changed fields are posted. Authentication/claim data never enters the job.
        values = {
            key: ("true" if value else "false") if type(value) is bool else value
            for key, value in changed.items()
        }
        log("Tag zuordnen", uid + " · " + ", ".join(changed))
        try:
            client.request(
                "POST", "/content/json/set/" + uid, params=params, data=values
            )
        except Exception:
            if read_tag(client, uid, overlay)["values"] != after:
                raise
        verified = read_tag(client, uid, overlay)
        if not verified["exists"] or verified["values"] != after:
            raise ValueError("Tag-Zuordnung konnte nicht bestätigt werden")
    return {
        "status": "Bestätigt",
        "message": "Tonie-/Tag-Zuordnung gespeichert und vollständig zurückgelesen",
    }
