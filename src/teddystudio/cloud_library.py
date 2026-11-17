"""Read-only inventory of actual server files, joined to models by audio ID + hash."""

from collections import defaultdict
import hashlib
import re
import time
from .domain import Book


def pair(header):
    audio = str(header.get("audioId", ""))
    digest = str(header.get("sha1Hash", "")).lower()
    return (
        (audio, digest)
        if audio.isdigit() and re.fullmatch(r"[0-9a-f]{40}", digest)
        else None
    )


def model_pairs(model):
    if not isinstance(model.get("audio_id", []), list) or not isinstance(
        model.get("hash", []), list
    ):
        return []
    return [
        (str(a), str(h).lower())
        for a, h in zip(model.get("audio_id", []), model.get("hash", []))
    ]


def inventory(client, overlay="", progress=lambda message: None):
    if overlay and not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", overlay):
        raise ValueError(
            "Overlay muss aus höchstens 15 Buchstaben, Ziffern, _ oder - bestehen."
        )
    custom = client.request("GET", "/api/toniesCustomJson", timeout=(5, 30)).json()
    if not isinstance(custom, list):
        raise ValueError("Unerwartete Custom-Tonie-Antwort")
    official = []
    warnings = []
    try:
        official = client.request("GET", "/api/toniesJson", timeout=(5, 30)).json()
        if not isinstance(official, list):
            raise ValueError("Unerwartete Katalogantwort")
    except Exception as exc:
        warnings.append("Katalogmetadaten nicht lesbar: " + str(exc))
    keys = {
        "model",
        "title",
        "series",
        "episodes",
        "no",
        "language",
        "category",
        "pic",
        "audio_id",
        "hash",
        "tracks",
    }
    custom = [
        {k: v for k, v in m.items() if k in keys} for m in custom if isinstance(m, dict)
    ]
    official = [
        {k: v for k, v in m.items() if k in keys}
        for m in official
        if isinstance(m, dict)
    ]
    custom_map = defaultdict(list)
    official_map = defaultdict(list)
    for models, target in ((custom, custom_map), (official, official_map)):
        for model in models:
            for key in model_pairs(model):
                target[key].append(model)
    found = {}
    tags = set()
    visited = set()
    files = 0
    for special in ("library", ""):
        pending = [""]
        while pending:
            folder = pending.pop()
            if (special, folder) in visited:
                continue
            visited.add((special, folder))
            if len(visited) > 10000:
                raise ValueError("Zu viele Verzeichnisse; Einlesen abgebrochen")
            progress(("Bibliothek" if special else "Inhalte") + ": " + folder)
            params = {"path": folder}
            if special:
                params["special"] = special
            if overlay:
                params["overlay"] = overlay
            try:
                entries = client.request(
                    "GET", "/api/fileIndexV2", params=params, timeout=(5, 30)
                ).json()["files"]
            except Exception as exc:
                warnings.append(
                    f"Ordner nicht lesbar ({special or 'content'}/{folder}): {exc}"
                )
                continue
            for item in entries:
                name = item.get("name", "")
                if (
                    not isinstance(name, str)
                    or name in {".", "..", ""}
                    or any(c in name for c in "/\\\0")
                ):
                    continue
                path = "/".join(filter(None, [folder, name]))
                if item.get("isDir"):
                    pending.append(path)
                    continue
                files += 1
                if files > 100000:
                    raise ValueError("Zu viele Dateien; Einlesen abgebrochen")
                if not special:
                    candidate = path.removesuffix(".json").replace("/", "")
                    if path.count("/") == 1 and re.fullmatch(
                        r"[0-9a-fA-F]{16}", candidate
                    ):
                        tags.add(candidate.lower())
                header = item.get("tafHeader", {})
                key = pair(header) if header.get("valid") else None
                if not key:
                    continue
                location = {
                    "special": special,
                    "path": path,
                    "overlay": overlay,
                    "bytes": int(item.get("size", 0)),
                }
                if key in found:
                    found[key]["locations"].append(location)
                    continue
                models = custom_map.get(key) or official_map.get(key) or []
                kind = (
                    "Custom"
                    if custom_map.get(key)
                    else "Katalog" if official_map.get(key) else "Unzugeordnet"
                )
                model = models[0] if len(models) == 1 else {}
                found[key] = {
                    "id": hashlib.sha256(
                        (client.url + overlay + key[0] + key[1]).encode()
                    ).hexdigest()[:24],
                    "audio_id": key[0],
                    "hash": key[1],
                    "locations": [location],
                    "model": model,
                    "aliases": models,
                    "title": model.get("title") or model.get("episodes") or name,
                    "series": model.get("series") or "Unzugeordnet",
                    "kind": kind,
                    "available": True,
                    "status": (
                        "Metadaten prüfen" if len(models) > 1 else "Audio vorhanden"
                    ),
                }
    rows = list(found.values())
    for model in custom:
        if any(key in found for key in model_pairs(model)):
            continue
        rows.append(
            {
                "id": "model-"
                + hashlib.sha256(str(model.get("model", "")).encode()).hexdigest()[:20],
                "title": model.get("title") or model.get("model") or "Ohne Titel",
                "series": model.get("series", ""),
                "kind": "Custom",
                "model": model,
                "aliases": [model],
                "locations": [],
                "available": False,
                "status": (
                    "Nicht gefunden / Bestand unvollständig"
                    if warnings
                    else "Nur Metadaten – Audio fehlt"
                ),
            }
        )
    from .tags import read_tag

    model_names = {str(m.get("model", "")).casefold(): m for m in custom + official}
    native_titles = {
        loc["path"].replace("/", "").lower(): entry["title"]
        for entry in rows
        for loc in entry["locations"]
        if not loc["special"]
    }
    source_titles = {
        ("lib://" if loc["special"] else "content://") + loc["path"]: entry["title"]
        for entry in rows
        for loc in entry["locations"]
    }
    tag_records = []
    for uid in sorted(tags):
        progress("Tonie-Zuordnungen lesen: " + uid)
        try:
            record = read_tag(client, uid, overlay)
            values = record["values"]
            model = model_names.get(values["tonie_model"].casefold(), {})
            tag_records.append(
                {
                    "uid": uid,
                    "overlay": overlay,
                    "model": values["tonie_model"],
                    "title": model.get("title")
                    or model.get("episodes")
                    or source_titles.get(values["source"])
                    or native_titles.get(uid)
                    or uid,
                    "source": values["source"],
                    "exists": record["exists"],
                }
            )
        except Exception as exc:
            warnings.append("Tonie-Zuordnung nicht lesbar: " + str(exc))
    return {
        "url": client.url,
        "overlay": overlay,
        "time": time.time(),
        "entries": rows,
        "tags": sorted(tags),
        "tag_records": tag_records,
        "warnings": warnings,
    }


def entry_book(entry, url, overlay=""):
    model = entry.get("model", {})
    episode = str(model.get("no", ""))
    return Book(
        "cloud-" + entry["id"],
        "",
        entry["title"],
        entry["series"],
        int(episode) if episode.isdigit() and int(episode) > 0 else None,
        language=model.get("language", "de"),
        category=model.get("category", "audio-play"),
        origin="Aus TeddyCloud " + url,
        remote_model=model.get("model", ""),
    )
