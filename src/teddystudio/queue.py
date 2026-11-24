"""Bounded process scheduler with per-job termination and isolated work directories."""

from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

from .storage import Settings, atomic_json, data_directory
from .processes import ProcessGuard


def run_child(request_path):
    """Headless entry point shared by source and packaged Windows applications."""
    request_path = Path(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    import tempfile

    native_temp = Path(request["work"]) / "native-temp"
    native_temp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(native_temp)
    os.environ["TEMP"] = os.environ["TMP"] = str(native_temp)
    from .jobs import execute

    def log(stage, message, progress=None):
        with (request_path.parent / "events.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            event = [stage, str(message)]
            if progress is not None:
                event.append(progress)
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    try:
        result = execute(request["kind"], request["payload"], request["work"], log)
        result["ok"] = True
    except BaseException as exc:
        result = {"ok": False, "message": str(exc), "traceback": traceback.format_exc()}
    atomic_json(request_path.parent / "result.json", result)
    return 0 if result["ok"] else 1


def terminate_tree(process, guard=None):
    """Reap the job and native tools before freeing its scheduler slot."""
    if guard and guard.handle:
        guard.close()
    elif os.name == "nt" and process.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=20)


class Worker:
    def __init__(self, store, changed=lambda: None, settings=None):
        self.store, self.changed = store, changed
        self.settings = settings
        self.pause = threading.Event()
        self.thread = None
        self._mutex = threading.RLock()
        self._active = {}
        self._actions = {}

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        with self._mutex:
            self.pause.clear()
            for row in self.store.jobs():
                if row["status"] == "Pausiert":
                    self.store.update_job(
                        row["id"],
                        "Wartet",
                        row["progress"],
                        "Wird mit geprüftem Arbeitsstand fortgesetzt",
                    )
            for identifier, action in list(self._actions.items()):
                if action == "pause":
                    self._actions[identifier] = "resume"
            if self.running:
                return
            self.thread = threading.Thread(
                target=self._run, name="TeddyShelf-queue", daemon=False
            )
            self.thread.start()

    def cancel(self, identifiers):
        self._request(identifiers, False)

    def delete(self, identifiers):
        self._request(identifiers, True)

    def pause_all(self):
        self.pause.set()
        self._request(
            [
                r["id"]
                for r in self.store.jobs()
                if r["status"] in {"Wartet", "Aktiv", "Wird pausiert"}
            ],
            False,
            paused=True,
        )

    def cancel_all(self):
        self.pause.set()
        self.cancel(
            [
                r["id"]
                for r in self.store.jobs()
                if r["status"]
                not in {
                    "Bestätigt",
                    "Lokal gespeichert",
                    "Fehlgeschlagen",
                    "Abgebrochen",
                }
            ]
        )

    def delete_all(self):
        self.pause.set()
        self.delete([r["id"] for r in self.store.jobs()])

    def _request(self, identifiers, delete, paused=False):
        with self._mutex:
            rows = {r["id"]: r for r in self.store.jobs()}
            for identifier in set(identifiers):
                if identifier not in rows:
                    continue
                row = rows[identifier]
                if identifier in self._active:
                    action = "delete" if delete else "pause" if paused else "cancel"
                    priority = {"resume": 0, "pause": 1, "cancel": 2, "delete": 3}
                    if priority[action] >= priority.get(
                        self._actions.get(identifier), -1
                    ):
                        self._actions[identifier] = action
                    else:
                        continue
                    self.store.update_job(
                        identifier,
                        (
                            "Wird gelöscht"
                            if delete
                            else "Wird pausiert" if paused else "Abbruch angefordert"
                        ),
                        row["progress"],
                        "Arbeitsprozess und Werkzeuge werden beendet",
                    )
                elif delete:
                    self.store.delete_jobs([identifier])
                elif row["status"] not in {"Bestätigt", "Lokal gespeichert"}:
                    status = "Pausiert" if paused else "Abgebrochen"
                    self.store.update_job(
                        identifier,
                        status,
                        row["progress"],
                        "Vom Benutzer angehalten; Arbeitsstand und vorhandene Dateien bleiben erhalten",
                    )
                    self.store.event(
                        identifier,
                        status,
                        "Keine weitere Verarbeitung. Bereits vorhandene Dateien werden nicht zurückgenommen.",
                    )
                else:
                    self.store.event(
                        identifier,
                        "Abbruch",
                        "Bereits fertig; keine laufende Verarbeitung. Gespeicherte Ergebnisse bleiben erhalten.",
                    )
        self.changed()

    def _launch(self, row):
        work = data_directory() / "work" / row["id"]
        attempt = work / ("runner-" + uuid.uuid4().hex)
        request = {
            "kind": row["kind"],
            "payload": json.loads(row["payload"]),
            "work": str(work),
        }
        atomic_json(attempt / "request.json", request)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).parents[2] / "launch.py"))
        command += ["--worker-job", str(attempt / "request.json")]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            guard = ProcessGuard(process)
        except Exception:
            terminate_tree(process)
            raise
        self._active[row["id"]] = {
            "process": process,
            "guard": guard,
            "attempt": attempt,
            "offset": 0,
            "request": request,
            "resources": self.resource_keys(row),
        }
        self.store.update_job(row["id"], "Aktiv", 0, "Arbeitsprozess gestartet")
        self.store.event(
            row["id"], "Gestartet", f"Isolierter Arbeitsprozess {process.pid}"
        )

    def _logs(self, identifier, item):
        path = item["attempt"] / "events.jsonl"
        if not path.exists():
            return False
        with path.open("rb") as stream:
            stream.seek(item["offset"])
            raw = stream.read()
        end = raw.rfind(b"\n") + 1
        for line in raw[:end].splitlines():
            stage, message, *reported = json.loads(line.decode("utf-8"))
            if item["request"]["kind"] == "Web-Download":
                item["progress"] = max(
                    item.get("progress", 0), float(reported[0]) if reported else 0
                )
                reported = [item["progress"]]
            self.store.event(identifier, stage, message)
            self.store.update_job(
                identifier,
                "Aktiv",
                (
                    max(0, min(0.99, float(reported[0])))
                    if reported
                    else {
                        "Prüfen": 0.05,
                        "Konvertieren": 0.25,
                        "Aufteilen": 0.4,
                        "Hochladen": 0.7,
                        "Modell speichern": 0.9,
                    }.get(stage, 0.1)
                ),
                stage + ": " + message,
            )
        item["offset"] += end
        return bool(end)

    def _finish(self, identifier, item):
        item["process"].wait()
        item["guard"].close()
        result_path = item["attempt"] / "result.json"
        result = (
            json.loads(result_path.read_text(encoding="utf-8"))
            if result_path.exists()
            else {
                "ok": False,
                "message": f"Arbeitsprozess unerwartet beendet ({item['process'].returncode})",
            }
        )
        if result["ok"]:
            if result.get("book"):
                from .domain import Book

                book = Book.from_dict(result["book"])
                from .library import within_folder

                old = next((b for b in self.store.books() if b.id == book.id), None)
                if any(
                    within_folder(book.folder, path)
                    for path in self.store.collection_exclusions()
                ):
                    self.store.event(
                        identifier,
                        "Sammlung unverändert",
                        "Der Zielordner wurde aus der Sammlung entfernt; Quelldateien bleiben erhalten",
                    )
                elif not old or "Manuell" not in old.origin:
                    self.store.save_book(book)
                else:
                    self.store.event(
                        identifier,
                        "Metadaten erhalten",
                        "Manuell bearbeiteter lokaler Sammlungseintrag bleibt erhalten",
                    )
            self.store.update_job(identifier, result["status"], 1, result["message"])
            self.store.event(identifier, result["status"], result["message"])
            if result.get("model"):
                payload = item["request"]["payload"]
                self.store.associate(
                    payload["book"]["id"],
                    payload["settings"]["url"],
                    result["model"],
                    result["fingerprint"],
                )
        else:
            self.store.update_job(identifier, "Fehlgeschlagen", 0, result["message"])
            self.store.event(
                identifier, "Fehlgeschlagen", result.get("traceback", result["message"])
            )
        self._clean_temp(item)

    @staticmethod
    def _clean_temp(item):
        import shutil
        from .workspace import clean_pcm

        work = Path(item["request"]["work"]).resolve()
        clean_pcm(work)
        target = work / "native-temp"
        if (
            target.exists()
            and not target.is_symlink()
            and target.resolve().parent == work
        ):
            shutil.rmtree(target, ignore_errors=True)

    def _run(self):
        from .jobs import exclusive_lock

        normal = False
        try:
            with exclusive_lock(self.store.path.with_suffix(".lock")):
                from .workspace import clean_inactive

                clean_inactive(data_directory() / "work")
                while True:
                    with self._mutex:
                        active = list(self._active.items())
                    for identifier, item in active:
                        with self._mutex:
                            action = self._actions.get(identifier)
                        if action:
                            terminate_tree(item["process"], item["guard"])
                            self._clean_temp(item)
                            with self._mutex:
                                action = self._actions.pop(identifier, action)
                                status = (
                                    "Wartet"
                                    if action == "resume"
                                    else (
                                        "Pausiert"
                                        if action == "pause"
                                        else "Abgebrochen"
                                    )
                                )
                                self.store.update_job(
                                    identifier,
                                    status,
                                    0,
                                    "Arbeitsprozess beendet; bereits gespeicherte Dateien bleiben erhalten",
                                )
                                self.store.event(
                                    identifier,
                                    status,
                                    "Arbeitsprozess einschließlich nativer Werkzeuge beendet",
                                )
                                if action == "delete":
                                    self.store.delete_jobs([identifier])
                                del self._active[identifier]
                            self.changed()
                        else:
                            with self._mutex:
                                if identifier in self._actions:
                                    continue
                                updated = self._logs(identifier, item)
                                if item["process"].poll() is not None:
                                    self._finish(identifier, item)
                                    del self._active[identifier]
                                    updated = True
                            if updated:
                                self.changed()
                    with self._mutex:
                        settings = self.settings or Settings.load()
                        limit = max(1, min(16, int(settings.parallel_jobs)))
                        waiting = [
                            r
                            for r in reversed(self.store.jobs())
                            if r["status"] == "Wartet"
                        ]
                        if not self.pause.is_set():
                            for row in waiting:
                                if len(self._active) >= limit:
                                    break
                                keys = self.resource_keys(row)
                                if any(
                                    keys & item["resources"]
                                    for item in self._active.values()
                                ):
                                    continue
                                self._launch(row)
                                self.changed()
                        if not self._active and (self.pause.is_set() or not waiting):
                            break
                    time.sleep(0.1)
            normal = True
        except Exception:
            self.store.event("", "Worker", traceback.format_exc())
        finally:
            for identifier, item in list(self._active.items()):
                terminate_tree(item["process"], item["guard"])
                self._clean_temp(item)
                self.store.update_job(
                    identifier,
                    "Unterbrochen",
                    0,
                    "Warteschlange beendet; erneut fortsetzbar",
                )
            with self._mutex:
                self._active.clear()
                self._actions.clear()
                restart = (
                    normal
                    and not self.pause.is_set()
                    and any(r["status"] == "Wartet" for r in self.store.jobs())
                )
                if restart:
                    self.thread = None
                    self.start()
            self.changed()

    @staticmethod
    def resource_keys(row):
        """Do not concurrently publish colliding local filenames in our queue."""
        from .domain import Book, NamingProfile
        import math

        payload = json.loads(row["payload"])
        if "web" in payload:
            from .web_downloads import output_path

            return {str(output_path(payload["web"])).casefold()}
        if "download" in payload:
            from .downloads import output_folder

            return {str(output_folder(payload)).casefold()}
        options = payload.get("audio")
        if not options or options["target"] == "Nur TeddyCloud":
            return set()
        book = Book.from_dict(payload["book"])
        profile = NamingProfile(**payload["profile"])
        destination = str(Path(options["destination"]).resolve()).casefold()
        naming = profile.render(book)
        names = [naming["filename"] + ".teddystudio.json"]
        from .media import AUDIO_MODES

        if options["mode"] in AUDIO_MODES[1:]:
            names.append(naming["filename"] + ".taf")
        else:
            count = max(1, math.ceil(book.duration / max(1, options["seconds"])))
            names.extend(
                profile.render(book, n + 1)["split_filename"] + "." + options["format"]
                for n in range(count)
            )
        return {destination + "/" + name.casefold() for name in names}
