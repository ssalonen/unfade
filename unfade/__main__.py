"""Command line: `serve` starts the web UI, `run` corrects a whole source without a browser,
`archive` and `verify` keep an archive of the originals. Messages follow UNFADE_LANG or the locale.

    python -m unfade serve
    python -m unfade run --source local:~/scans --dest local:~/corrected
    python -m unfade run --source immich-tag:Albums/1978 --dest immich --dest-tag Corrected
    python -m unfade run --source immich-folder:/mnt/photos/old --recursive --dest immich --dest-album "Old photos"

`run` creates the same kind of job as the web UI, with the proposed groups, and exports every image without
approval; the job can then be reviewed, regrouped and re-exported in the web UI.
"""
from __future__ import annotations

import argparse
import sys

from unfade import color_methods, immich, jobs, locations
from unfade.i18n import t


def _source(spec: str, recursive: bool) -> dict:
    kind, _, value = spec.partition(":")
    if kind == "local":
        return {"kind": "local", "path": value, "recursive": recursive}
    if kind == "immich-folder":
        return {"kind": kind, "path": value, "recursive": recursive}
    if kind in {"immich-tag", "immich-album"}:
        return {"kind": kind, "name": value}
    raise SystemExit(t("--source: use local:PATH, immich-folder:PATH, immich-tag:TAG or immich-album:NAME"))


def _destination(args) -> dict:
    kind, _, value = args.dest.partition(":")
    if kind == "local":
        return {"kind": "local", "path": value, "suffix": args.suffix, "keep_folders": args.keep_folders, "format": args.format}
    if kind == "immich":
        album_id = ""
        if args.dest_album:
            albums = [album for album in immich.client().albums() if album["albumName"] == args.dest_album]
            if len(albums) != 1:
                raise SystemExit(t("No single album is named {name!r}", name=args.dest_album))
            album_id = albums[0]["id"]
        return {"kind": "immich", "tag": args.dest_tag, "album_id": album_id, "format": args.format, "stack": not args.no_stack,
                "copy_details": not args.no_copy_details, "replace_previous": args.replace_previous,
                "replace_original": args.replace_original}
    raise SystemExit(t("--dest: use local:PATH or immich (with --dest-tag / --dest-album)"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m unfade", description=t("Colour correction for faded photos"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help=t("start the web UI"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    run = commands.add_parser("run", help=t("correct and export a whole source at once"))
    run.add_argument("--source", required=True)
    run.add_argument("--recursive", action="store_true", help=t("include subfolders"))
    run.add_argument("--dest", required=True)
    run.add_argument("--dest-tag", default="")
    run.add_argument("--dest-album", default="")
    run.add_argument("--suffix", default="", help=t("file name suffix of a local copy, for example --suffix=-corrected"))
    run.add_argument("--keep-folders", action="store_true")
    run.add_argument("--method", choices=color_methods.METHODS, default=color_methods.DEFAULT_METHOD)
    run.add_argument("--format", choices=locations.FORMATS, default="jxl", help=t("jxl: lossless 16-bit (default), jpeg: small and lossy"))
    run.add_argument("--no-stack", action="store_true", help=t("do not stack the Immich copy with the original"))
    run.add_argument("--replace-original", action="store_true",
                     help=t("replace the Immich original with the corrected copy: details move over, the original goes to the trash"))
    run.add_argument("--replace-previous", action="store_true", help=t("move the earlier copy of the same image to Immich's trash"))
    run.add_argument("--no-copy-details", action="store_true", help=t("do not copy the original's albums, tags and Immich details"))
    archive_command = commands.add_parser("archive", help=t("archive the originals with their metadata and correction recipes"))
    archive_command.add_argument("--source", required=True)
    archive_command.add_argument("--recursive", action="store_true", help=t("include subfolders"))
    archive_command.add_argument("--to", required=True, help=t("archive folder"))
    verify_command = commands.add_parser("verify", help=t("check the archive's files against SHA256SUMS"))
    verify_command.add_argument("archive")
    args = parser.parse_args(argv)

    if args.command == "archive":
        from unfade import archive
        try:
            result = archive.run(_source(args.source, args.recursive), args.to,
                                 lambda done, total, name, added: print(f"[{done}/{total}] {name}" + (f" ({t('new')})" if added else ""), flush=True))
        except (ValueError, OSError, immich.ImmichError, archive.ArchiveError) as error:
            print(t(str(error)), file=sys.stderr)
            return 2
        print(t("{originals} originals: {added} new, {unchanged} already archived; {codes} code versions",
                originals=result["originals"], added=result["added"], unchanged=result["unchanged"], codes=len(result["codes"])))
        for warning in result["warnings"]:
            print(t("WARNING: {warning}", warning=warning), file=sys.stderr)
        return 1 if result["warnings"] else 0
    if args.command == "verify":
        from unfade import archive
        problems = archive.verify(args.archive)
        for problem in problems:
            print(problem)
        print(t("The archive is intact") if not problems else t("{count} problems", count=len(problems)))
        return 1 if problems else 0

    if args.command == "serve":
        import uvicorn
        uvicorn.run("unfade.web:app", host=args.host, port=args.port)
        return 0

    try:
        job = jobs.create(_source(args.source, args.recursive), _destination(args), args.method)
    except (ValueError, OSError, immich.ImmichError) as error:
        print(t(str(error)), file=sys.stderr)
        return 2
    print(t("Job {id}: {images} images, {groups} groups", id=job["id"], images=len(job["items"]), groups=len(job["groups"])))

    def progress(done, total, name, error):
        print(f"[{done}/{total}] {name}" + (f"  {t('ERROR: {error}', error=error)}" if error else ""), flush=True)

    # Nobody reviews a headless run, so every group is exported without approval.
    result = jobs.run_export(job["id"], progress=progress, only_approved=False)["export"]
    print(t("Written {written}, skipped {skipped}, errors {errors}",
            written=result.get("written", 0), skipped=result.get("skipped", 0), errors=len(result["errors"])))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
