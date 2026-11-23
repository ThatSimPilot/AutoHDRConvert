"""ConversionService: watch a folder and convert new .jxr files one at a time on a worker thread.

No Qt, no tkinter. Events go to `on_event` from non-UI threads (StateChanged from start()/stop() comes from the
caller's thread). watchdog's dispatch thread only enqueues; a single worker thread does the converting.
"""
import logging
import os
import queue
import re
import threading
import time
from dataclasses import replace
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .convert import convert_file
from .models import (ConversionFinished, ConversionResult, ConversionStarted, Problems, ServiceEvent, Settings,
                     StateChanged)
from .paths import APP_NAME, HDRFIX_MISSING, clean_path, find_hdrfix

log = logging.getLogger(APP_NAME)
_BAD_NAME_CHARS = re.compile(r'[<>:"|?*]')


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _normalised(s: Settings) -> Settings:
    return replace(s, input_dir=clean_path(s.input_dir), output_dir=clean_path(s.output_dir))


def _failure(src, message):
    return ConversionResult(False, src, "", message, False, time.time())


def _output_problem(out):
    """Why `out` can't be created, or "". Creates nothing: walks up to the nearest folder that exists."""
    if not out:
        return ""
    path = os.path.abspath(out)
    if _BAD_NAME_CHARS.search(os.path.splitdrive(path)[1]):
        return 'Folder names can\'t contain < > : " | ? *'
    exact = path
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return "That folder can't be created."
        path = parent
    if os.path.isdir(path):
        return ""
    return "That's a file, not a folder." if path == exact else "That folder can't be created."


def validate_settings(s: Settings) -> Problems:
    """Checks only; never creates anything (start() does that)."""
    inp = clean_path(s.input_dir)
    return Problems(
        input="Choose a folder to watch." if not inp else ("" if os.path.isdir(inp) else "That folder doesn't exist."),
        output=_output_problem(clean_path(s.output_dir)),
        tool="" if find_hdrfix() else HDRFIX_MISSING,
    )


class _Handler(FileSystemEventHandler):
    """Only enqueues. Carries the session token of the start() that created it, so a stale observer is ignored."""

    def __init__(self, service, token):
        self._service, self._token = service, token

    def on_created(self, event):
        self._add(event, "src_path")

    def on_moved(self, event):  # files are often renamed into place
        self._add(event, "dest_path")

    def _add(self, event, attr):
        try:  # an exception escaping here would silently kill watchdog's thread while the UI says "Watching"
            path = getattr(event, attr)
            if not event.is_directory and os.path.splitext(path)[1].lower() == ".jxr":
                self._service._enqueue(path, token=self._token)
        except Exception:
            log.error("Error handling a file event", exc_info=True)


def _stop_observer(observer):
    try:
        observer.stop()
        observer.join()  # ~1 s on Windows, hence off the caller's thread
    except Exception:
        log.error("Error stopping the folder watcher", exc_info=True)


class ConversionService:
    def __init__(self, on_event: Callable[[ServiceEvent], None], settings: Settings | None = None):
        self._on_event = on_event
        self._settings = _normalised(settings or Settings())  # replaced wholesale by configure(), never mutated
        self._lock = threading.Lock()  # guards everything below
        self._queue = queue.Queue()  # (src, is_retry) items; None tells the worker to exit
        self._inflight = set()  # keys of queued + running paths
        self._observer = None
        self._session = 0  # bumped by every start()/stop(); handlers of an older session are ignored
        self._running = False
        self._closed = False
        self._worker = None
        self._child = None  # the running hdrfix process, so shutdown() can terminate it
        self._retiring = []  # threads that are stopping old observers

    @property
    def running(self) -> bool:
        return self._running

    # -- public API ---------------------------------------------------------------------------------------------

    def configure(self, settings: Settings) -> None:
        """output_dir/delete_original apply from the next file; a changed input_dir restarts a running watcher."""
        s = _normalised(settings)
        with self._lock:
            old, self._settings = self._settings, s
            restart = self._running and _key(old.input_dir) != _key(s.input_dir)
        if restart:
            self.start()

    def start(self) -> str:
        """(Re)start watching. Returns "" on success, else a user-facing reason; always emits StateChanged."""
        with self._lock:
            if self._closed:
                return "The app is closing."
            s = self._settings
        self._halt()  # idempotent restart: retire any current observer and queued work first
        p = validate_settings(s)
        error, observer = p.input or p.output or p.tool, None
        if not error:
            try:
                os.makedirs(s.output_dir or s.input_dir, exist_ok=True)
            except OSError as e:
                error = f"Can't create the output folder: {e.strerror or e}"
        if not error:
            with self._lock:
                token = self._session
            observer = Observer()
            try:
                observer.schedule(_Handler(self, token), s.input_dir, recursive=False)
                observer.start()
            except Exception as e:
                observer, error = None, f"Couldn't start watching that folder: {e}"
        if observer:
            with self._lock:
                self._observer, self._running = observer, True
            log.info(f"Watching {s.input_dir}")
        else:
            log.error(f"Not watching: {error}")
        self._emit(StateChanged(observer is not None, error))
        return error

    def stop(self) -> None:
        """Returns at once (the watcher winds down on a background thread). A running conversion finishes."""
        self._halt()
        log.info("Watching stopped")
        self._emit(StateChanged(False, ""))

    def retry(self, src: str) -> None:
        self._enqueue(src, retry=True)

    def shutdown(self, timeout: float = 3.0) -> None:
        """Stop, let the current conversion finish for up to `timeout` s, then terminate hdrfix; join the threads."""
        deadline = time.monotonic() + timeout
        with self._lock:
            self._closed = True
        self._halt()
        with self._lock:
            worker, retiring = self._worker, list(self._retiring)
            if worker:
                self._queue.put(None)
        if worker:
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker.is_alive():  # still converting past the grace period: don't leave hdrfix running after quit
                with self._lock:
                    child = self._child
                if child:
                    try:
                        child.terminate()
                    except OSError as e:
                        log.error(f"Could not terminate hdrfix: {e}")
                worker.join(1.0)
        for t in retiring:
            t.join(max(0.0, deadline + 1.0 - time.monotonic()))

    # -- internals ----------------------------------------------------------------------------------------------

    def _emit(self, event):
        try:
            self._on_event(event)
        except Exception:
            log.error("on_event handler raised", exc_info=True)

    def _halt(self):
        """Invalidate the current session, drop queued (not yet started) work and retire the observer."""
        with self._lock:
            self._session += 1
            self._running = False
            observer, self._observer = self._observer, None
            sentinel = False
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    sentinel = True
                else:
                    self._inflight.discard(_key(item[0]))
            if sentinel:
                self._queue.put(None)
            if observer:
                t = threading.Thread(target=_stop_observer, args=(observer,), name="autohdr-observer-stop", daemon=True)
                self._retiring = [x for x in self._retiring if x.is_alive()] + [t]
                t.start()

    def _enqueue(self, src, token=None, retry=False):
        key = _key(src)
        with self._lock:
            if self._closed or key in self._inflight or (token is not None and token != self._session):
                return
            self._inflight.add(key)
            self._queue.put((src, retry))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="autohdr-worker", daemon=True)
                self._worker.start()

    def _set_child(self, proc):
        with self._lock:
            self._child = proc

    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            src, retry = item
            try:
                result = self._process(src, retry)
            except Exception:  # the worker must outlive any single file
                log.error(f"Unexpected error processing {src}", exc_info=True)
                result = _failure(src, "Unexpected error while converting")
            with self._lock:  # eligible again before the UI hears about it, so Retry can't be deduped away
                self._inflight.discard(_key(src))
            if result:
                self._emit(ConversionFinished(result))

    def _process(self, src, retry):
        """Returns the result to report, or None for a file that vanished before we started (silently skipped)."""
        with self._lock:
            s = self._settings
        if not os.path.exists(src):
            if retry:
                return _failure(src, "The original file no longer exists")
            log.info(f"Skipped {src}: it was removed before conversion started")
            return None
        self._emit(ConversionStarted(src))
        hdrfix = find_hdrfix()
        if hdrfix is None:
            return _failure(src, HDRFIX_MISSING)
        return convert_file(src, s.output_dir or os.path.dirname(src), hdrfix, s.delete_original, self._set_child)
