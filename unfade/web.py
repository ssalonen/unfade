"""Web UI for unfade: choose a source and destination, group, correct and approve, export.

The group board (static/groups.js) handles dragging between groups and Undo/Redo; the correction
preview (static/correct.js) draws the live slider preview, clipping marks, histogram and ΔE readings.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from unfade import color_methods, i18n, immich, jobs, locations, render
from unfade.i18n import t

STATIC = Path(__file__).parent / "static"
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates",
                             context_processors=[lambda request: {"lang": i18n.language()}])
PAGE_SIZE = 48


def asset_version() -> str:
    newest = max((path.stat().st_mtime_ns for path in STATIC.glob("*.*")), default=0)
    return format(newest, "x")


TEMPLATE_GLOBALS: dict[str, Any] = {
    "asset_version": asset_version, "t": t, "label": i18n.label,
    "languages": i18n.LANGUAGE_NAMES, "script_texts": i18n.script_texts,
}
TEMPLATES.env.globals.update(TEMPLATE_GLOBALS)
LANGUAGE_COOKIE = "unfade-lang"


def _immich_choices() -> dict:
    """Folders, tags and albums for the pickers, or the reason Immich is unavailable."""
    if not immich.configured():
        return {"available": False, "reason": t("Set IMMICH_URL and IMMICH_API_KEY to choose Immich folders, tags and albums.")}
    try:
        client = immich.client()
        return {"available": True, "user": client.me().get("name", ""), "folders": client.folders(), "tags": client.tags(), "albums": client.albums()}
    except immich.ImmichError as error:
        return {"available": False, "reason": t(str(error))}


def _home_context(error: str = "", form: dict | None = None) -> dict:
    return {
        "jobs": jobs.list_jobs(), "immich": _immich_choices(), "methods": color_methods.METHOD_LABELS,
        "roots": locations.allowed_roots(), "source_label": locations.source_label, "formats": locations.FORMAT_LABELS,
        "destination_label": locations.destination_label, "error": error,
        "form": form or {"source_kind": "local", "destination_kind": "local", "method": color_methods.DEFAULT_METHOD,
                         "destination_tag": t("Colour-corrected"), "suggest_groups": "on",
                         "format": "jxl", "original_mode": "stack", "copy_details": "on"},
    }


def _load(job_id: str) -> dict:
    try:
        return jobs.load(job_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=t(str(error))) from error


def _page_of(indexes: list[int], page: int) -> tuple[int, int, list[int]]:
    pages = max(1, -(-len(indexes) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    return page, pages, indexes[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]


def _correction_url(job_id: str, group_id: int | None, saved: str = "", **extra) -> str:
    query = "&".join(f"{key}={value}" for key, value in {"group": group_id, **extra, "saved": saved}.items() if value not in (None, ""))
    return f"/jobs/{job_id}?{query}#correction"


def create_app() -> FastAPI:
    app = FastAPI(title="Unfade")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def language_and_caching(request: Request, call_next):
        # ?lang= switches the language and is remembered in a cookie; else the browser decides.
        chosen = request.query_params.get("lang", "")
        if chosen not in i18n.LANGUAGES:
            chosen = ""
        code = chosen or request.cookies.get(LANGUAGE_COOKIE, "")
        if code not in i18n.LANGUAGES:
            code = i18n.from_accept_language(request.headers.get("accept-language", ""))
        token = i18n.use(code)
        try:
            response = await call_next(request)
        finally:
            i18n.reset(token)
        if chosen:
            response.set_cookie(LANGUAGE_COOKIE, chosen, max_age=365 * 24 * 3600, samesite="lax")
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, "home.html", _home_context())

    @app.get("/api/folders")
    def folders(path: str = "") -> JSONResponse:
        try:
            return JSONResponse(locations.browse(path))
        except (ValueError, OSError) as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error

    @app.post("/jobs")
    async def create_job(request: Request):
        form = {key: str(value) for key, value in (await request.form()).items()}
        source = {
            "local": {"kind": "local", "path": form.get("source_path", ""), "recursive": "source_recursive" in form},
            "immich-folder": {"kind": "immich-folder", "path": form.get("immich_folder", ""), "recursive": "immich_folder_recursive" in form},
            "immich-tag": {"kind": "immich-tag", "id": form.get("immich_tag", "")},
            "immich-album": {"kind": "immich-album", "id": form.get("immich_album", "")},
        }.get(form.get("source_kind", ""), {})
        destination = {
            "local": {"kind": "local", "path": form.get("destination_path", ""), "suffix": form.get("destination_suffix", ""),
                      "keep_folders": "keep_folders" in form, "format": form.get("format", "jxl")},
            "immich": {"kind": "immich", "tag": form.get("destination_tag", ""), "album_id": form.get("destination_album", ""),
                       "stack": form.get("original_mode", "stack") == "stack", "replace_original": form.get("original_mode") == "replace",
                       "copy_details": "copy_details" in form,
                       "replace_previous": "replace_previous" in form, "format": form.get("format", "jxl")},
        }.get(form.get("destination_kind", ""), {})
        method = form.get("method", color_methods.DEFAULT_METHOD)
        try:
            # Listing a large folder or tag blocks, so it runs off the event loop.
            job = await run_in_threadpool(jobs.create, source, destination, method, "suggest_groups" in form)
        except (ValueError, OSError, immich.ImmichError) as error:
            # Show the form again with what was chosen, rather than a bare error page.
            context = await run_in_threadpool(_home_context, t(str(error)), form)
            return TEMPLATES.TemplateResponse(request, "home.html", context, status_code=422)
        return RedirectResponse(f"/jobs/{job['id']}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str, group: int | None = None, page: int = 1, item: int | None = None) -> HTMLResponse:
        job = _load(job_id)
        job["export"]["state"] = jobs.export_state(job)
        filled = [entry for entry in job["groups"] if jobs.members(job, entry["id"])]
        # One group at a time: the one asked for, else the first still waiting for approval.
        current = next((entry for entry in filled if entry["id"] == group), None)
        current = current or next((entry for entry in filled if not entry["approved"]), None) or filled[0]
        page, pages, indexes = _page_of(jobs.members(job, current["id"]), page)
        selected = item if item is not None and 0 <= item < len(job["items"]) and job["items"][item]["group"] == current["id"] else indexes[0]
        entry = job["items"][selected]
        return TEMPLATES.TemplateResponse(request, "job.html", {
            "job": job, "filled": filled, "current": current, "members": {group["id"]: jobs.members(job, group["id"]) for group in job["groups"]},
            "page": page, "pages": pages, "indexes": indexes, "selected": selected, "entry": entry,
            "methods": color_methods.METHOD_LABELS, "limits": color_methods.ADJUSTMENT_LIMITS,
            "adjustment": entry["adjustments"] or color_methods.DEFAULT_ADJUSTMENTS, "pending": jobs.pending(job),
            "revision": jobs.revision(job), "approved_count": sum(1 for group in filled if group["approved"]),
            "source_label": locations.source_label(job["source"]), "destination_label": locations.destination_label(job["destination"]),
            "saved": request.query_params.get("saved", ""),
        })

    @app.post("/jobs/{job_id}/grouping")
    def save_grouping(job_id: str, state: str = Form(), revision: str = Form()) -> RedirectResponse:
        try:
            jobs.save_grouping(job_id, json.loads(state), revision)
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=422, detail=t("Invalid group change")) from error
        except jobs.GroupingConflict as error:
            raise HTTPException(status_code=409, detail=t(str(error))) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        return RedirectResponse(f"/jobs/{job_id}?saved=grouping#groups", status_code=303)

    @app.post("/jobs/{job_id}/groups/{group_id}")
    def save_group(job_id: str, group_id: int, label: str = Form(), method: str = Form()) -> RedirectResponse:
        stored = next((group["label"] for group in _load(job_id)["groups"] if group["id"] == group_id), None)
        if stored is not None and label.strip() == i18n.label(stored):
            label = stored  # an untouched suggested name keeps following the language
        try:
            jobs.save_group(job_id, group_id, label, method)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        return RedirectResponse(_correction_url(job_id, group_id, "method"), status_code=303)

    @app.post("/jobs/{job_id}/groups/{group_id}/approve")
    def approve_group(job_id: str, group_id: int) -> RedirectResponse:
        try:
            following = jobs.approve_group(job_id, group_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        if following is None:
            return RedirectResponse(_correction_url(job_id, group_id, "all"), status_code=303)
        return RedirectResponse(_correction_url(job_id, following, "approved"), status_code=303)

    @app.post("/jobs/{job_id}/items/{index}")
    def save_item(
        job_id: str, index: int, page: int = Form(1),
        strength: int = Form(100), black: int = Form(0), white: int = Form(0), temperature: int = Form(0),
        reset: str | None = Form(None),
    ) -> RedirectResponse:
        values = None if reset else {"strength": strength, "black": black, "white": white, "temperature": temperature}
        try:
            group_id = jobs.set_adjustments(job_id, index, values)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        return RedirectResponse(_correction_url(job_id, group_id, "adjustments", page=page, item=index), status_code=303)

    @app.get("/jobs/{job_id}/thumbnail/{index}")
    def thumbnail(job_id: str, index: int) -> Response:
        job = _load(job_id)
        try:
            content = locations.thumbnail(job["source"], jobs.item(job, index)).read_bytes()
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except (ValueError, OSError, immich.ImmichError) as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        # Immich may answer with WebP; the local cache is always JPEG.
        media_type = "image/webp" if content[:4] == b"RIFF" else "image/jpeg"
        return Response(content, media_type=media_type)

    @app.get("/jobs/{job_id}/preview/{index}")
    def preview(job_id: str, index: int, view: str = "adjusted") -> Response:
        job = _load(job_id)
        try:
            entry = jobs.item(job, index)
            content = render.preview_bytes(jobs.source_path(job, index), jobs.method_of(job, entry), entry["adjustments"], view)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        except (ValueError, OSError, immich.ImmichError) as error:
            raise HTTPException(status_code=422, detail=t(str(error))) from error
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/jobs/{job_id}/groups/{group_id}/delta-e")
    def delta_e(job_id: str, group_id: int, page: int = 1) -> JSONResponse:
        # For static/correct.js; covers one page of the group.
        job = _load(job_id)
        try:
            method = jobs.group(job, group_id)["method"]
        except LookupError as error:
            raise HTTPException(status_code=404, detail=t(str(error))) from error
        _, _, indexes = _page_of(jobs.members(job, group_id), page)
        items = {}
        for index in indexes:
            try:
                items[str(index)] = render.delta_e(jobs.source_path(job, index))
            except (ValueError, OSError, LookupError, immich.ImmichError):
                items[str(index)] = None
        readings = [reading for reading in items.values() if reading]
        average = {
            part: {key: sum(reading[part][key] for reading in readings) / len(readings) for key in readings[0][part]}
            for part in ("original", "pairs")
        } if readings else None
        return JSONResponse({"method": method, "average": average, "items": items})

    @app.post("/jobs/{job_id}/export")
    def export(job_id: str, force: str | None = Form(None)) -> RedirectResponse:
        _load(job_id)
        try:
            jobs.start_export(job_id, bool(force))
        except ValueError as error:
            raise HTTPException(status_code=409, detail=t(str(error))) from error
        return RedirectResponse(f"/jobs/{job_id}#export", status_code=303)

    @app.get("/jobs/{job_id}/export")
    def export_status(job_id: str) -> JSONResponse:
        job = _load(job_id)
        return JSONResponse({**job["export"], "state": jobs.export_state(job)})

    @app.post("/jobs/{job_id}/delete")
    def delete_job(job_id: str) -> RedirectResponse:
        try:
            jobs.delete(job_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=t(str(error))) from error
        return RedirectResponse("/", status_code=303)

    return app


app = create_app()
