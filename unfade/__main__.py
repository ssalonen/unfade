"""Command line: `serve` starts the web UI, `run` corrects a whole source without a browser.

    python -m unfade serve
    python -m unfade run --source local:~/kuvat --dest local:~/korjatut
    python -m unfade run --source immich-tag:Kuvat/K001 --dest immich --dest-tag Kuvat/Korjattu
    python -m unfade run --source immich-folder:/mnt/photos/vanhat --recursive --dest immich --dest-album "Kuvat"

`run` creates the same kind of job as the web UI, with the proposed groups, and exports every image without
approval; the job can then be reviewed, regrouped and re-exported in the web UI.
"""
from __future__ import annotations

import argparse
import sys

from unfade import color_methods, immich, jobs, locations


def _source(spec: str, recursive: bool) -> dict:
    kind, _, value = spec.partition(":")
    if kind == "local":
        return {"kind": "local", "path": value, "recursive": recursive}
    if kind == "immich-folder":
        return {"kind": kind, "path": value, "recursive": recursive}
    if kind in {"immich-tag", "immich-album"}:
        return {"kind": kind, "name": value}
    raise SystemExit("--source: käytä local:POLKU, immich-folder:POLKU, immich-tag:TUNNISTE tai immich-album:NIMI")


def _destination(args) -> dict:
    kind, _, value = args.dest.partition(":")
    if kind == "local":
        return {"kind": "local", "path": value, "suffix": args.suffix, "keep_folders": args.keep_folders, "format": args.format}
    if kind == "immich":
        album_id = ""
        if args.dest_album:
            albums = [album for album in immich.client().albums() if album["albumName"] == args.dest_album]
            if len(albums) != 1:
                raise SystemExit(f"Albumia {args.dest_album!r} ei löydy yksiselitteisesti")
            album_id = albums[0]["id"]
        return {"kind": "immich", "tag": args.dest_tag, "album_id": album_id, "format": args.format, "stack": not args.no_stack,
                "copy_details": not args.no_copy_details, "replace_previous": args.replace_previous,
                "replace_original": args.replace_original}
    raise SystemExit("--dest: käytä local:POLKU tai immich (ja --dest-tag / --dest-album)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m unfade", description="Haalistuneiden valokuvien värikorjaus")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="käynnistä selainkäyttöliittymä")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    run = commands.add_parser("run", help="korjaa ja vie koko lähde kerralla")
    run.add_argument("--source", required=True)
    run.add_argument("--recursive", action="store_true", help="myös alikansiot")
    run.add_argument("--dest", required=True)
    run.add_argument("--dest-tag", default="")
    run.add_argument("--dest-album", default="")
    run.add_argument("--suffix", default="", help="paikallisen kopion nimen pääte, esim. --suffix=-korjattu")
    run.add_argument("--keep-folders", action="store_true")
    run.add_argument("--method", choices=color_methods.METHODS, default=color_methods.DEFAULT_METHOD)
    run.add_argument("--format", choices=locations.FORMATS, default="jxl", help="jxl: häviötön 16-bit (oletus), jpeg: pieni ja häviöllinen")
    run.add_argument("--no-stack", action="store_true", help="älä pinoa Immich-kopiota alkuperäisen kanssa")
    run.add_argument("--replace-original", action="store_true",
                     help="korvaa Immich-alkuperäinen korjatulla: tiedot siirtyvät, alkuperäinen roskakoriin")
    run.add_argument("--replace-previous", action="store_true", help="siirrä saman kuvan aiempi kopio Immichin roskakoriin")
    run.add_argument("--no-copy-details", action="store_true", help="älä kopioi alkuperäisen albumeja, tunnisteita ja Immich-tietoja")
    archive_command = commands.add_parser("archive", help="arkistoi alkuperäiset metatietoineen ja korjausohjeineen")
    archive_command.add_argument("--source", required=True)
    archive_command.add_argument("--recursive", action="store_true", help="myös alikansiot")
    archive_command.add_argument("--to", required=True, help="arkistohakemisto")
    verify_command = commands.add_parser("verify", help="tarkista arkiston tiedostot SHA256SUMS-luetteloa vasten")
    verify_command.add_argument("archive")
    args = parser.parse_args(argv)

    if args.command == "archive":
        from unfade import archive
        try:
            result = archive.run(_source(args.source, args.recursive), args.to,
                                 lambda done, total, name, added: print(f"[{done}/{total}] {name}{' (uusi)' if added else ''}", flush=True))
        except (ValueError, OSError, immich.ImmichError, archive.ArchiveError) as error:
            print(error, file=sys.stderr)
            return 2
        print(f"Alkuperäisiä {result['originals']}: uusia {result['added']}, ennestään {result['unchanged']}; koodiversioita {len(result['codes'])}")
        for warning in result["warnings"]:
            print(f"VAROITUS: {warning}", file=sys.stderr)
        return 1 if result["warnings"] else 0
    if args.command == "verify":
        from unfade import archive
        problems = archive.verify(args.archive)
        for problem in problems:
            print(problem)
        print("Arkisto kunnossa" if not problems else f"{len(problems)} ongelmaa")
        return 1 if problems else 0

    if args.command == "serve":
        import uvicorn
        uvicorn.run("unfade.web:app", host=args.host, port=args.port)
        return 0

    try:
        job = jobs.create(_source(args.source, args.recursive), _destination(args), args.method)
    except (ValueError, OSError, immich.ImmichError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Työ {job['id']}: {len(job['items'])} kuvaa, {len(job['groups'])} ryhmää")

    def progress(done, total, name, error):
        print(f"[{done}/{total}] {name}" + (f"  VIRHE: {error}" if error else ""), flush=True)

    # Nobody reviews a headless run, so every group is exported without approval.
    result = jobs.run_export(job["id"], progress=progress, only_approved=False)["export"]
    print(f"Kirjoitettu {result.get('written', 0)}, ohitettu {result.get('skipped', 0)}, virheitä {len(result['errors'])}")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
