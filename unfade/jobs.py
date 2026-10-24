"""A job: one source, one destination and correction groups, kept as JSON.

A group decides the correction method, each image is still fitted on its own and may carry its
own adjustments, and a group is approved once its images look right. Any change to a group's
method, members or an image's adjustments withdraws that group's approval.
Groups are proposed from the source's folders, with strong brightness or colour outliers split
into groups of their own for review.

The image list is a snapshot taken when the job is created. Export writes the images of approved
groups whose settings changed since their last export; a local copy is overwritten, an Immich
upload of changed settings is a new asset.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from unfade import color_methods, fingerprint, immich, locations, render
from unfade.grouping import file_hash, outlier_buckets, visual_metrics
from unfade.i18n import N_, t

_lock = threading.RLock()
_exports: dict[str, threading.Thread] = {}


class GroupingConflict(Exception):
    """Another page changed the job's groups since they were loaded."""


def _directory() -> Path:
    return locations.data_directory() / "jobs"


def _path(job_id: str) -> Path:
    if not job_id.isalnum():
        raise LookupError(t("Job not found"))
    return _directory() / f"{job_id}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def save(job: dict) -> None:
    with _lock:
        _directory().mkdir(parents=True, exist_ok=True)
        target = _path(job["id"])
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, target)


def load(job_id: str) -> dict:
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LookupError(t("Job not found")) from error


def list_jobs() -> list[dict]:
    if not _directory().is_dir():
        return []
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in _directory().glob("*.json")]
    return sorted(jobs, key=lambda job: job["created"], reverse=True)


def delete(job_id: str) -> None:
    """Forget a job; the source images and exported copies stay where they are."""
    with _lock:
        if job_id in _exports and _exports[job_id].is_alive():
            raise ValueError(t("An export is running"))
        _path(job_id).unlink(missing_ok=True)


def _suggested_groups(source: dict, items: list[dict], method: str) -> list[dict]:
    """One group per source folder; within a folder of five or more, strong outliers apart.

    Folders count only for folder sources: an Immich tag or album is already a chosen set, and
    the folders of Immich uploads are random (upload/<user>/1f/7b/...).
    """
    by_folder: dict[str, list[int]] = {}
    folders = source["kind"] in {"local", "immich-folder"}
    for index, entry in enumerate(items):
        by_folder.setdefault(entry["folder"] if folders else "", []).append(index)
    metrics = {}
    for index, entry in enumerate(items):
        try:
            with locations.grouping_image(source, entry) as image:
                metrics[index] = visual_metrics(image)
        except (OSError, ValueError, immich.ImmichError):
            continue
    buckets = outlier_buckets(metrics, [[index for index in members if index in metrics] for members in by_folder.values()])
    groups: dict[tuple[str, str], dict] = {}
    for index, entry in enumerate(items):
        key = (entry["folder"] if folders else "", buckets.get(index, ""))
        if key not in groups:
            folder = (Path(key[0]).name or N_("Top folder")) if len(by_folder) > 1 else ""
            label = " · ".join(filter(None, (folder, key[1]))) or N_("All images")
            groups[key] = {"id": len(groups) + 1, "label": label[:100], "method": method, "approved": False}
        entry["group"] = groups[key]["id"]
    return list(groups.values())


def create(source: dict, destination: dict, method: str = color_methods.DEFAULT_METHOD, suggest: bool = True) -> dict:
    if method not in color_methods.METHODS:
        raise ValueError(t("Unknown correction method"))
    source = locations.validate_source(source)
    destination = locations.validate_destination(destination, source)
    listed = locations.list_items(source)
    items = [{**{key: value for key, value in entry.items() if key not in ("unfade_copy", "copy_of", "trashed")},
              "folder": entry.get("folder", str(Path(entry["name"]).parent)), "group": 1, "adjustments": None, "exported": None}
             for entry in listed if not entry.get("unfade_copy")]
    if not items:
        raise ValueError(t("The source has no images to correct") if listed else t("The source has no images"))
    groups = _suggested_groups(source, items, method) if suggest else [{"id": 1, "label": N_("All images"), "method": method, "approved": False}]
    job = {
        "id": secrets.token_hex(6), "created": _now(), "source": source, "destination": destination, "method": method,
        "groups": groups, "next_group": max(group["id"] for group in groups) + 1, "items": items,
        "skipped_copies": len(listed) - len(items),
        "export": {"state": "idle", "done": 0, "total": 0, "errors": []},
    }
    save(job)
    return job


def item(job: dict, index: int) -> dict:
    if not 0 <= index < len(job["items"]):
        raise LookupError(t("Image not found in this job"))
    return job["items"][index]


def group(job: dict, group_id: int) -> dict:
    found = next((entry for entry in job["groups"] if entry["id"] == group_id), None)
    if not found:
        raise LookupError(t("Group not found in this job"))
    return found


def members(job: dict, group_id: int) -> list[int]:
    return [index for index, entry in enumerate(job["items"]) if entry["group"] == group_id]


def method_of(job: dict, entry: dict) -> str:
    return group(job, entry["group"])["method"]


def revision(job: dict) -> str:
    """Fingerprint of the groups and membership, so a stale grouping page cannot overwrite a newer one."""
    state = [[(entry["id"], entry["label"]) for entry in job["groups"]], [entry["group"] for entry in job["items"]]]
    return hashlib.sha256(json.dumps(state, ensure_ascii=False).encode()).hexdigest()


def save_grouping(job_id: str, payload: dict, expected_revision: str) -> None:
    """Replace the groups and membership in one go, after the board's client-side Undo/Redo.

    The payload is the shared group board's: groups as {"key": "g-<id>" | "new-<n>", "label"} and
    assignments from the card id (the image's index, zero-padded) to a group key.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list) or not isinstance(payload.get("assignments"), dict):
        raise ValueError(t("Invalid group change"))  # noqa: TRY004 - invalid form data uses ValueError
    labels = {}
    for entry in payload["groups"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("key"), str) or not isinstance(entry.get("label"), str):
            raise ValueError(t("Invalid group"))  # noqa: TRY004 - keep the form validation error contract
        key, label = entry["key"], entry["label"].strip()
        if not (re.fullmatch(r"g-[1-9]\d*|new-[A-Za-z0-9_-]+", key) and label and len(label) <= 100) or key in labels:
            raise ValueError(t("Invalid group id or name"))
        labels[key] = label
    if not labels:
        raise ValueError(t("A job needs at least one group"))
    with _lock:
        job = load(job_id)
        if revision(job) != expected_revision:
            raise GroupingConflict(t("The groups were changed on another page. Reload the page before saving."))
        try:
            assignments = {int(card): key for card, key in payload["assignments"].items()}
        except ValueError as error:
            raise ValueError(t("Invalid image")) from error
        if set(assignments) != set(range(len(job["items"]))) or not set(assignments.values()) <= set(labels):
            raise ValueError(t("Every image must belong to one group"))
        existing = {entry["id"]: entry for entry in job["groups"]}
        retained = {int(key[2:]) for key in labels if key.startswith("g-")}
        if retained - set(existing):
            raise ValueError(t("The group does not belong to this job"))
        ids, groups = {}, []
        for key, label in labels.items():
            if key.startswith("new-"):
                entry = {"id": job["next_group"], "label": label, "method": job["method"], "approved": False}
                job["next_group"] += 1
            else:
                entry = {**existing[int(key[2:])], "label": label}
            ids[key] = entry["id"]
            groups.append(entry)
        changed = set()
        for index, key in assignments.items():
            if job["items"][index]["group"] != ids[key]:
                changed.update((job["items"][index]["group"], ids[key]))
                job["items"][index]["group"] = ids[key]
        for entry in groups:
            if entry["id"] in changed:
                entry["approved"] = False
        job["groups"] = groups
        save(job)


def save_group(job_id: str, group_id: int, label: str, method: str) -> None:
    """Rename a group and choose its method; a new method withdraws the approval."""
    label = label.strip()
    if not label or len(label) > 100:
        raise ValueError(t("Give the group a name (at most 100 characters)"))
    if method not in color_methods.METHODS:
        raise ValueError(t("Unknown correction method"))
    with _lock:
        job = load(job_id)
        entry = group(job, group_id)
        if entry["method"] != method:
            entry["approved"] = False
        entry.update(label=label, method=method)
        save(job)


def approve_group(job_id: str, group_id: int) -> int | None:
    """Approve one group and return the next group that still needs approval."""
    with _lock:
        job = load(job_id)
        if not members(job, group_id):
            raise ValueError(t("An empty group cannot be approved"))
        group(job, group_id)["approved"] = True
        save(job)
    pending = [entry["id"] for entry in job["groups"] if not entry["approved"] and members(job, entry["id"])]
    later = [candidate for candidate in pending if candidate > group_id]
    return (later or pending or [None])[0]


def set_adjustments(job_id: str, index: int, adjustments: dict | None) -> int:
    """Store one image's adjustments (None resets them), withdraw its group's approval, return the group."""
    checked = color_methods.validate_adjustments(adjustments) if adjustments is not None else None
    with _lock:
        job = load(job_id)
        entry = item(job, index)
        entry["adjustments"] = None if checked in (None, color_methods.DEFAULT_ADJUSTMENTS) else checked
        group(job, entry["group"])["approved"] = False
        save(job)
        return entry["group"]


def source_path(job: dict, index: int) -> Path:
    return locations.local_file(job["source"], item(job, index))


def _signature(job: dict, entry: dict, path: Path) -> str:
    return json.dumps([method_of(job, entry), entry["adjustments"], render.version(path), job["destination"]], sort_keys=True)


def approved(job: dict, entry: dict) -> bool:
    return group(job, entry["group"])["approved"]


def pending(job: dict) -> int:
    """How many approved images an export would write now (unchanged earlier exports are skipped)."""
    return sum(1 for entry in job["items"] if approved(job, entry) and (
        not entry["exported"] or entry["exported"].get("method") != method_of(job, entry)
        or entry["exported"].get("adjustments") != entry["adjustments"]))


def export_state(job: dict) -> str:
    """The export's state; "interrupted" when it says running but no export runs in this process
    (the app was stopped mid-export). Starting again picks up where it stopped."""
    state = job["export"]["state"]
    if state == "running" and not (job["id"] in _exports and _exports[job["id"]].is_alive()):
        return "interrupted"
    return state


def start_export(job_id: str, force: bool = False) -> None:
    with _lock:
        running = _exports.get(job_id)
        if running and running.is_alive():
            raise ValueError(t("An export is already running"))
        job = load(job_id)
        job["export"] = {"state": "running", "done": 0, "total": len(job["items"]), "errors": [], "started": _now()}
        save(job)
        # The export reports in the language of the request that started it.
        thread = threading.Thread(target=contextvars.copy_context().run, args=(run_export, job_id, force), daemon=True,
                                  name=f"unfade-export-{job_id}")
        _exports[job_id] = thread
        thread.start()


def wait(job_id: str, timeout: float | None = None) -> None:
    thread = _exports.get(job_id)
    if thread:
        thread.join(timeout)


def _update(job_id: str, change) -> dict:
    with _lock:
        job = load(job_id)
        change(job)
        save(job)
        return job


METADATA_KEY = "unfade"


def _original_uuid(client: immich.Immich, asset_id: str) -> str:
    """The UUID kept in an Immich original's metadata, created on first export."""
    value = client.metadata(asset_id, METADATA_KEY) or {}
    if not value.get("uuid"):
        value = {**value, "uuid": str(uuid.uuid4())}
        client.set_metadata(asset_id, METADATA_KEY, value)
    return value["uuid"]


def _copy_metadata(recipe: dict, sha256: str) -> dict:
    """What a corrected copy's Immich metadata says: its own UUID and digest, how to find its
    original, and the recipe, so an archive can list it without downloading the copy."""
    origin = recipe["source"]
    keys = ("uuid", "immich_asset", "name", "path", "sha256", "sha1", "size", "pixel_sha256", "pixel_format", "orientation", "phash", "dhash", "document_id")
    return {"uuid": recipe["uuid"], "sha256": sha256, "method": recipe["method"], "phash": recipe["phash"], "dhash": recipe["dhash"],
            "recipe": {key: recipe[key] for key in ("method", "curve", "adjustments", "code")},
            "source": {key: origin[key] for key in keys if origin.get(key) not in (None, "")}}


def _local_time(instant: str, zone: str | None) -> str:
    """An Immich UTC timestamp written in the asset's own time zone, so the offset carries the zone."""
    moment = datetime.fromisoformat(instant)
    offset = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", zone or "")
    if offset:
        sign = 1 if offset[1] == "+" else -1
        moment = moment.astimezone(timezone(sign * timedelta(hours=int(offset[2]), minutes=int(offset[3] or 0))))
    elif zone:
        try:
            moment = moment.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return moment.isoformat(timespec="milliseconds")


def _copy_details(source: immich.Immich, client: immich.Immich, original_id: str, copy_id: str,
                  destination_album: str | None) -> tuple[list[str], list[str]]:
    """Give the copy the original's albums, favourite, rating, description, date and location,
    and return the original's tag ids for the caller to add with the others in one update.

    These live in Immich (database or sidecar), not in the original file, so the upload alone does
    not carry them. Returns the parts that could not be copied, for the export's warnings.
    """
    problems = []
    try:
        original = source.asset(original_id)
        albums = [album["id"] for album in source.albums_with(original_id)]
    except immich.ImmichError as failure:
        return [t("could not read the original's details: {failure}", failure=failure)], []
    exif = original.get("exifInfo") or {}
    fields = {"isFavorite": bool(original.get("isFavorite"))}
    if exif.get("description"):
        fields["description"] = exif["description"]
    if exif.get("rating") is not None:
        fields["rating"] = exif["rating"]
    if exif.get("dateTimeOriginal"):
        fields["dateTimeOriginal"] = _local_time(exif["dateTimeOriginal"], exif.get("timeZone"))
    if exif.get("latitude") is not None and exif.get("longitude") is not None:
        fields.update(latitude=exif["latitude"], longitude=exif["longitude"])
    steps = [(t("details"), lambda: client.update_asset(copy_id, fields))]
    steps += [(t("album {album}", album=album), lambda album=album: client.add_to_album(album, [copy_id]))
              for album in albums if album != destination_album]
    for label, step in steps:
        try:
            step()
        except immich.ImmichError as failure:
            problems.append(t("{part} not copied: {failure}", part=label, failure=failure))
    return problems, [tag["id"] for tag in original.get("tags") or []]


def _replace_original(client: immich.Immich, living: str, original_id: str, copy_id: str) -> list[str]:
    """Let the new copy take the place of the original (or of the copy that already replaced it).

    Immich's copy endpoint moves albums, shared links, stack and favourite; then the old asset goes
    to the trash. An original in an external library is left in place: Immich cannot delete its
    file, and the next library scan would import it again.
    """
    try:
        client.copy_asset(living, copy_id)
    except immich.ImmichError as failure:
        return [t("albums, shared links and stack not moved ({failure}); the old one was not deleted", failure=failure)]
    if living != original_id:
        return _replace_previous(client, {living: True}, original_id, None, True)
    try:
        if client.asset(original_id).get("libraryId"):
            return [t("the original is in an external library, so it was not deleted (Immich does not delete its file)")]
        client.trash([original_id])
    except immich.ImmichError as failure:
        return [t("could not move the original to the trash: {failure}", failure=failure)]
    return []


def _replace_previous(client: immich.Immich, candidates: dict[str, bool], original_id: str,
                      stack_id: str | None, check_metadata: bool) -> list[str]:
    """Move earlier corrected copies of one original to Immich's trash, once the new copy is in place.

    `candidates` maps asset ids to whether this job itself recorded them as its upload. A recorded
    one goes unless its Immich metadata names another original; any other asset (found in the
    original's stack) goes only when its metadata says it is a unfade copy of this original, so
    the original and versions made elsewhere stay. Trash, not a permanent delete: restorable until
    Immich empties the trash.
    """
    problems, doomed = [], []
    for asset_id, recorded_here in candidates.items():
        recorded = None
        if check_metadata:
            try:
                recorded = ((client.metadata(asset_id, METADATA_KEY) or {}).get("source") or {}).get("immich_asset")
            except immich.ImmichError:
                recorded = None
        if recorded == original_id or (recorded_here and recorded is None):
            doomed.append(asset_id)
        elif recorded_here:
            problems.append(t("earlier copy {asset} not deleted: its details refer to another original", asset=asset_id))
    for asset_id in doomed:
        if stack_id:
            try:
                client.remove_from_stack(stack_id, asset_id)
            except immich.ImmichError as failure:
                problems.append(t("could not take earlier copy {asset} out of the stack: {failure}", asset=asset_id, failure=failure))
    if doomed:
        try:
            client.trash(doomed)
        except immich.ImmichError as failure:
            problems.append(t("could not move earlier copies {assets} to the trash: {failure}", assets=', '.join(doomed), failure=failure))
    return problems


def run_export(job_id: str, force: bool = False, progress=None, only_approved: bool = True) -> dict:
    """Write every image that needs it; one failing image is recorded and the rest continue.

    Images of unapproved groups are left for later, except when the command line asks for all.
    """
    job = _update(job_id, lambda job: job.update(export={"state": "running", "done": 0, "total": len(job["items"]), "errors": [], "started": _now()}))
    destination = job["destination"]
    try:
        client = immich.client() if destination["kind"] == "immich" else None
        source_client = immich.client() if job["source"]["kind"] != "local" else None
        tag_id = client.ensure_tag(destination["tag"])["id"] if client and destination.get("tag") else None
    except immich.ImmichError as failure:
        return _update(job_id, lambda job, failure=failure: job["export"].update(state="failed", finished=_now(), errors=[str(failure)]))
    warnings: list[str] = []
    metadata = {"supported": True}

    def remember(call):
        """Run an Immich metadata call; after the first refusal, warn once and skip the rest."""
        if not metadata["supported"]:
            return None
        try:
            return call()
        except immich.ImmichError as failure:
            metadata["supported"] = False
            warnings.append(t("Immich did not store identifiers in the images' metadata (needs Immich v2.5 or newer): {failure}", failure=failure))
            return None
    taken: set[str] = set()
    stems = [locations.output_stem(entry, destination, taken) for entry in job["items"]]
    written = skipped = waiting = 0
    for index, entry in enumerate(job["items"]):
        record = None
        error = None
        try:
            if only_approved and not approved(job, entry):
                waiting += 1
                continue
            path = source_path(job, index)
            signature = _signature(job, entry, path)
            if not force and entry["exported"] and entry["exported"].get("signature") == signature:
                skipped += 1
                continue
            origin = {"name": entry["name"], "sha256": file_hash(path), "sha1": fingerprint.file_sha1(path), "size": path.stat().st_size}
            if job["source"]["kind"] == "local":
                origin["path"] = str(path)
            else:
                origin.update(path=str(Path(entry["folder"]) / entry["name"]), immich_asset=entry["key"])
                # The original keeps one UUID in Immich's metadata; its file is not touched.
                assert source_client is not None
                source_uuid = remember(lambda entry=entry: _original_uuid(source_client, entry["key"]))
                if source_uuid:
                    origin["uuid"] = source_uuid
            # An upload cut off earlier reuses its UUID: the same bytes come back from Immich as a
            # duplicate of the copy it already has, instead of a second copy.
            pending = entry.get("pending") if (entry.get("pending") or {}).get("signature") == signature else None
            content, suffix, recipe = render.corrected_file(path, method_of(job, entry), entry["adjustments"], destination.get("format", "jxl"),
                                                            origin, pending["uuid"] if pending else None)
            if client and not pending:
                _update(job_id, lambda job, index=index, uuid=recipe["uuid"], signature=signature: job["items"][index].update(pending={"uuid": uuid, "signature": signature}))
            name = stems[index] + suffix
            copy_sha256 = hashlib.sha256(content).hexdigest()
            if client:
                uploaded = client.upload(Path(name).name, content, entry.get("created"))
                if not client.wait_for_metadata(uploaded["id"]):
                    warnings.append(f"{entry['name']}: " + t("Immich did not read the copy's details within a minute; its tags may be lost"))
                if destination.get("album_id"):
                    client.add_to_album(destination["album_id"], [uploaded["id"]])
                target = uploaded["id"]
                remember(lambda target=target, recipe=recipe, copy_sha256=copy_sha256: client.set_metadata(target, METADATA_KEY, _copy_metadata(recipe, copy_sha256)))
                previous = (entry["exported"] or {}).get("target")
                previous = previous if previous and previous not in (target, entry["key"]) else None
                # In replace mode an earlier copy has taken the original's place, and may since have
                # gained albums or tags of its own, so it is the one the new copy takes over from.
                living = previous if destination.get("replace_original") and previous else entry["key"]
                tag_ids = [tag_id] if tag_id else []
                if destination.get("copy_details"):
                    assert source_client is not None
                    problems, original_tags = _copy_details(source_client, client, living, target, destination.get("album_id"))
                    warnings.extend(f"{entry['name']}: {problem}" for problem in problems)
                    tag_ids += [found for found in original_tags if found not in tag_ids]
                if tag_ids:
                    # All tags in one update, after the details: see Immich.add_tags.
                    client.add_tags(tag_ids, [target])
                if destination.get("replace_original"):
                    warnings.extend(f"{entry['name']}: {problem}" for problem in _replace_original(client, living, entry["key"], target))
                stack, members = None, []
                if destination.get("stack") and target != entry["key"]:
                    try:
                        # Immich merges a stack only when its primary is named: the original's current
                        # stack (made by this job or an earlier run) joins the new one.
                        current = (client.asset(entry["key"]).get("stack") or {}).get("primaryAssetId")
                        named = list(dict.fromkeys(item for item in (target, previous, current, entry["key"]) if item))
                        stack = client.stack(named)
                        members = [asset["id"] for asset in stack.get("assets") or []]
                    except immich.ImmichError as failure:
                        error = f"{entry['name']}: " + t("uploaded, but stacking with the original failed: {failure}", failure=failure)
                if destination.get("replace_previous") and not destination.get("replace_original"):
                    # This job's last upload is known; other stack members count only if their metadata says so.
                    candidates = {member: False for member in members if member not in (target, entry["key"])}
                    if previous:
                        candidates[previous] = True
                    warnings.extend(f"{entry['name']}: {problem}" for problem in _replace_previous(
                        client, candidates, entry["key"], stack.get("id") if stack else None, metadata["supported"]))
            else:
                output = Path(destination["path"]) / name
                if output.resolve() == path.resolve():
                    raise ValueError(t("The destination would replace the original image"))
                output.parent.mkdir(parents=True, exist_ok=True)
                partial = output.with_name(output.name + ".part")
                partial.write_bytes(content)
                partial.replace(output)
                target = str(output)
            record = {"signature": signature, "target": target, "at": _now(),
                      "method": method_of(job, entry), "adjustments": entry["adjustments"], "recipe": recipe,
                      "sha256": copy_sha256, "name": Path(name).name}
            written += 1
        except (OSError, ValueError, LookupError, MemoryError, immich.ImmichError) as failure:
            error = f"{entry['name']}: {t(str(failure))}"
        except Exception as failure:  # noqa: BLE001 - record unforeseen bugs so the export can finish
            error = f"{entry['name']}: " + t("unexpected error {kind}: {failure}", kind=type(failure).__name__, failure=failure)
        finally:
            def step(job, index=index, record=record, error=error):
                if record:
                    job["items"][index]["exported"] = record
                    job["items"][index].pop("pending", None)
                if error:
                    job["export"]["errors"].append(error)
                job["export"]["done"] = index + 1
            _update(job_id, step)
            if progress:
                progress(index + 1, len(job["items"]), entry["name"], error)

    def finish(job):
        job["export"].update(state="done", finished=_now(), written=written, skipped=skipped, waiting=waiting, warnings=warnings)
    return _update(job_id, finish)
