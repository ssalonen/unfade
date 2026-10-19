"""Finnish translations, keyed by the English text in the code, templates and scripts.

tests/test_i18n.py checks that every t("...") text has an entry here and that no entry is unused.
"""

FI = {
    # Setup page
    "Colour correction": "Värikorjaus",
    "Colour correction for faded photos. The source is a local folder or an Immich folder, tag or album; corrected copies go to a local folder or to Immich. Originals are never modified.":
        "Haalistuneiden valokuvien värikorjaus. Lähde on paikallinen kansio tai Immichin kansio, tunniste tai albumi; korjatut kopiot menevät paikalliseen kansioon tai Immichiin. Alkuperäisiä kuvia ei muokata.",
    "Jobs": "Työt",
    "{images} images · {groups} groups · created {created}": "{images} kuvaa · {groups} ryhmää · luotu {created}",
    "exported {time}": "viety {time}",
    "export running": "vienti käynnissä",
    "Delete job {name}": "Poista työ {name}",
    "Delete job": "Poista työ",
    "Deleting a job deletes neither the source images nor the exported copies.": "Työn poisto ei poista lähdekuvia eikä vietyjä kopioita.",
    "New job": "Uusi työ",
    "Immich is not available:": "Immich ei ole käytettävissä:",
    "Connected to Immich as {user}.": "Immich yhdistetty käyttäjänä {user}.",
    "Connected to Immich.": "Immich yhdistetty.",
    "1. Source": "1. Lähde",
    "Local folder": "Paikallinen kansio",
    "Immich folder": "Immich-kansio",
    "Immich tag": "Immich-tunniste",
    "Immich album": "Immich-albumi",
    "Folder": "Kansio",
    "photos": "kuvat",
    "corrected": "korjatut",
    "Include subfolders": "Myös alikansiot",
    "Start typing a path": "Aloita kirjoittaa polkua",
    "Tag": "Tunniste",
    "Choose a tag": "Valitse tunniste",
    "Album": "Albumi",
    "Choose an album": "Valitse albumi",
    "2. Destination": "2. Kohde",
    "Immich (tag or album)": "Immich (tunniste tai albumi)",
    "A missing folder is created. To export into an Immich folder, choose a folder that an Immich external library watches.":
        "Puuttuva kansio luodaan. Immich-kansioon viet valitsemalla kansion, jota Immichin ulkoinen kirjasto seuraa.",
    "File name suffix (optional)": "Nimen pääte (vapaaehtoinen)",
    "-corrected": "-korjattu",
    "Keep subfolders": "Säilytä alikansiot",
    "Tag for corrected images": "Tunniste korjatuille kuville",
    "For example Photos/Corrected": "Esim. Kuvat/Korjattu",
    "A new tag is created. Write a child tag with a slash, for example Photos/Corrected.":
        "Uusi tunniste luodaan. Alatunniste kirjoitetaan kauttaviivalla, esim. Kuvat/Korjattu.",
    "Album (optional)": "Albumi (vapaaehtoinen)",
    "No album": "Ei albumia",
    "The original in Immich (Immich sources only)": "Alkuperäinen Immichissä (vain Immich-lähteelle)",
    "Stack the corrected copy with the original (copy on top)": "Pinoa korjattu alkuperäisen kanssa (korjattu päällimmäisenä)",
    "Replace the original with the corrected copy": "Korvaa alkuperäinen korjatulla",
    "Separate image, original left as it is": "Erillinen kuva, alkuperäinen ennallaan",
    "When replacing, the corrected copy takes over the original's albums, tags, shared links, stack, favourite, rating, description, date and location, and the original moves to Immich's trash. It can be restored from there until the trash is emptied; after that the recipe can no longer be recomputed without a master kept elsewhere. An original in an external library is not deleted. People are detected again; comments and memories do not move.":
        "Korvatessa korjattu kuva saa alkuperäisen albumit, tunnisteet, jakolinkit, pinon, suosikkimerkinnän, arvion, kuvauksen, päiväyksen ja sijainnin, ja alkuperäinen siirtyy Immichin roskakoriin. Sieltä sen voi palauttaa, kunnes roskakori tyhjennetään; sen jälkeen korjauksen ohjetta ei voi enää laskea uudelleen ilman muualla säilytettyä masteria. Ulkoisen kirjaston alkuperäistä ei poisteta. Henkilöt tunnistetaan uudelleen; kommentit ja muistot eivät siirry.",
    "Copy the original's albums, tags, favourite, rating, description, date and location (Immich sources only)":
        "Kopioi alkuperäisen albumit, tunnisteet, suosikki, arvio, kuvaus, päiväys ja sijainti (vain Immich-lähteelle)",
    "Move the earlier corrected copy of the same image to Immich's trash once the new one is exported":
        "Siirrä saman kuvan aiempi korjattu kopio Immichin roskakoriin, kun uusi on viety",
    "Applies only to copies made by unfade, never to the original. In replace mode the earlier copy is always replaced.":
        "Koskee vain unfaden tekemiä kopioita, ei alkuperäistä. Korvaustilassa aiempi kopio korvataan aina.",
    "Immich picks the storage path of an upload itself, so an Immich destination is a tag or an album.":
        "Immich päättää ladatun kuvan tallennuspolun itse, joten Immich-kohteessa valitaan tunniste tai albumi.",
    "File format": "Tallennusmuoto",
    "JPEG XL is lossless and 16-bit. A source with a colour profile other than sRGB is saved as a lossless 16-bit PNG, so the profile is kept. Every copy records the correction recipe and the original's identity in its XMP, so it can be recomputed from the original.":
        "JPEG XL on häviötön ja 16-bittinen. Jos lähteellä on muu kuin sRGB-väriprofiili, kuva tallennetaan häviöttömänä 16-bittisenä PNG:nä, jotta profiili säilyy. Jokaiseen kopioon kirjataan XMP-tietoihin korjauksen ohje ja alkuperäisen tunniste, joten kopion voi laskea uudelleen alkuperäisestä.",
    "3. Groups and method": "3. Ryhmät ja menetelmä",
    "Suggest groups: one per folder, with clearly different brightness or colour in a review group of its own":
        "Ehdota ryhmät: kansio kerrallaan, selvästi poikkeava kirkkaus tai väri omaan tarkistettavaan ryhmään",
    "default": "oletus",
    "Groups can be edited, and each group's method changed, in the preview.": "Ryhmiä voi muokata ja kunkin ryhmän menetelmän vaihtaa esikatselussa.",
    "Create the job and open the preview": "Luo työ ja avaa esikatselu",
    "Allowed local folders: {roots} (environment variable UNFADE_ROOTS).": "Sallitut paikalliset kansiot: {roots} (ympäristömuuttuja UNFADE_ROOTS).",
    "Language": "Kieli",
    # Job page
    "{count} images": "{count} kuvaa",
    "1 image": "1 kuva",
    "1. Arrange the correction groups": "1. Järjestä värikorjausryhmät",
    "Saved": "Tallennettu",
    "A group decides the correction method; each image is still fitted on its own. The suggestion separates folders and clearly different brightness or colour, though the subject itself may explain a difference. Dragging a selected image moves all selected images of its group together. Changes are saved with one click.":
        "Ryhmä määrää korjausmenetelmän; jokainen kuva sovitetaan silti erikseen. Ehdotus erottaa kansiot ja selvästi poikkeavan kirkkauden tai värin, mutta kuva-aihekin voi selittää eron. Kun vedät valittua kuvaa, kaikki valitut kuvat sen lähtöryhmästä siirtyvät yhdessä. Muutokset tallennetaan yhdellä painalluksella.",
    "{count} images selected": "{count} kuvaa valittu",
    "Move the selected to group": "Siirrä valitut ryhmään",
    "Move the selected": "Siirrä valitut",
    "Save groups": "Tallenna ryhmät",
    "Approved group": "Hyväksytty ryhmä",
    "Correction group": "Värikorjausryhmä",
    "approved": "hyväksytty",
    "awaiting approval": "odottaa hyväksyntää",
    "Delete group {name}": "Poista ryhmä {name}",
    "Delete group": "Poista ryhmä",
    "Select {name}": "Valitse {name}",
    "Enlarge {name}": "Suurenna kuva {name}",
    "Own adjustments": "Omat säädöt",
    "Drop images into this group": "Pudota kuvia tähän ryhmään",
    "Add a group": "Lisää ryhmä",
    "Name of the new group": "Uuden ryhmän nimi",
    "For example indoor shots": "Esim. sisäkuvat",
    "Close the large image": "Sulje suuri kuva",
    "Close": "Sulje",
    "Previous": "Edellinen",
    "Next": "Seuraava",
    "Move the images to group": "Siirrä kuvat ryhmään",
    "Cancel": "Peruuta",
    "Move the images and delete the group": "Siirrä kuvat ja poista ryhmä",
    "2. Correct and approve the groups": "2. Korjaa ja hyväksy ryhmät",
    "Each image is fitted on its own. The group decides the correction method; an image's own adjustments go on top. Changing the method or an adjustment withdraws the group's approval.":
        "Jokainen kuva sovitetaan erikseen. Ryhmä määrää korjausmenetelmän; kuvan omat säädöt lisätään sen päälle. Menetelmän vaihto tai kuvan säätö peruu ryhmän hyväksynnän.",
    "Correction groups": "Värikorjausryhmät",
    "Approved": "Hyväksytty",
    "In progress": "Kesken",
    "Group name": "Ryhmän nimi",
    "Correction method": "Korjausmenetelmä",
    "Change from the original": "Muutos alkuperäiseen",
    "Save name and method": "Tallenna nimi ja menetelmä",
    "All groups approved. Export the corrected images below.": "Kaikki ryhmät hyväksytty. Vie korjatut kuvat alla.",
    "Approved {done} / {total} groups.": "Hyväksytty {done} / {total} ryhmää.",
    "Approve again": "Hyväksy uudelleen",
    "Approve the group": "Hyväksy ryhmä",
    "Show the original": "Näytä alkuperäinen",
    "Show clipping": "Näytä leikkautuminen",
    "magenta = 255 (white)": "magenta = 255 (valkoinen)",
    "cyan = 0 (black)": "syaani = 0 (musta)",
    "{name} corrected": "{name} korjattu",
    "exported": "viety",
    "Pages of the group": "Ryhmän sivut",
    "Page {page} / {pages}": "Sivu {page} / {pages}",
    "Selected image": "Valittu kuva",
    "{name} preview": "{name} esikatselu",
    "Histogram by channel": "Histogrammi kanavittain",
    "Clips at 255:": "Leikkautuu 255:",
    "Clips at 0:": "Leikkautuu 0:",
    "Difference ΔE for this image (without own adjustments)": "Ero ΔE tälle kuvalle (ilman omia säätöjä)",
    "in use": "käytössä",
    "Between methods": "Menetelmien ero",
    "ΔE tells how large a difference is, not which result is right. Below ~2 is hard to see, above ~10 is clearly visible.":
        "ΔE kertoo eron suuruuden, ei sitä, mikä on oikein. Alle ~2 on vaikea erottaa, yli ~10 näkyy selvästi.",
    "Adjustments for this image": "Säädöt tälle kuvalle",
    "Strength": "Voimakkuus",
    "Black point": "Mustapiste",
    "White point": "Valkopiste",
    "Temperature (blue ↔ yellow)": "Lämpötila (sininen ↔ keltainen)",
    "Save the image's adjustments": "Tallenna kuvan säädöt",
    "Back to automatic": "Palauta automaattinen",
    "Adjustments saved. The group needs to be approved again.": "Säädöt tallennettu. Ryhmä on hyväksyttävä uudelleen.",
    "3. Export the corrected images": "3. Vie korjatut kuvat",
    "Destination:": "Kohde:",
    "Every exported image is uploaded to Immich as a new asset.": "Jokainen viety kuva ladataan Immichiin uutena kuvana.",
    "An earlier copy of the same name in the destination is replaced.": "Samanniminen aiempi kopio kohteessa korvataan.",
    "To export now:": "Vietäviä nyt:",
    "Only images of approved groups are exported; images whose method and adjustments have not changed since the last export are skipped.":
        "Vain hyväksyttyjen ryhmien kuvat viedään; kuvat, joiden menetelmä ja säädöt eivät ole muuttuneet edellisen viennin jälkeen, ohitetaan.",
    "Export running: {done} / {total}": "Vienti käynnissä: {done} / {total}",
    "Export finished {time}: written {written}, skipped {skipped}": "Vienti valmis {time}: kirjoitettu {written}, ohitettu {skipped}",
    "awaiting approval {count}": "odottaa hyväksyntää {count}",
    "errors {count}": "virheitä {count}",
    "The export failed.": "Vienti epäonnistui.",
    "The export was interrupted ({done} / {total}). Start it again: images already exported are skipped, and the one cut off is not duplicated.":
        "Vienti keskeytyi ({done} / {total}). Käynnistä se uudelleen: jo viedyt kuvat ohitetaan, eikä kesken jääneestä tule kaksoiskappaletta.",
    "Export the approved images": "Vie hyväksytyt kuvat",
    "Export unchanged images again too": "Vie myös muuttumattomat uudelleen",
    # Browser scripts
    "approval is withdrawn on saving": "hyväksyntä raukeaa tallennettaessa",
    "1 image selected": "1 kuva valittu",
    "Unsaved group changes. Undo and Redo work until you save.": "Tallentamattomia ryhmämuutoksia. Undo ja Redo toimivat tallennukseen asti.",
    "{images} moved to group {target}.": "{images} siirretty ryhmään {target}.",
    "Own group": "Oma ryhmä",
    "Group {name} added.": "Ryhmä {name} lisätty.",
    "Group {name} deleted; {images} moved to group {target}.": "Ryhmä {name} poistettu; {images} siirretty ryhmään {target}.",
    "Group {name} deleted.": "Ryhmä {name} poistettu.",
    "Delete {name}?": "Poista {name}?",
    "{images} belong to this group. Choose the group to move them to. Undo brings back the group and its images.":
        "{images} kuuluu tähän ryhmään. Valitse ryhmä, johon ne siirretään. Undo palauttaa ryhmän ja sen kuvat.",
    "corrected preview": "korjauksen esikatselu",
    "original": "alkuperäinen",
    "Group: {name}": "Ryhmä: {name}",
    "Selected images": "Valitut kuvat",
    "Drop here · {images}": "Pudota tähän · {images}",
    "Save the group changes before adjusting or exporting.": "Tallenna ryhmämuutokset ennen säätöjä tai vientiä.",
    "Saving groups…": "Tallennetaan ryhmiä…",
    "Saving failed": "Tallennus epäonnistui",
    "preview missing": "esikatselu puuttuu",
    "clips {share}": "leikkautuu {share}",
    "Browse folders": "Selaa kansioita",
    "Choose this folder": "Valitse tämä kansio",
    "Parent folder": "Yläkansio",
    "The folder could not be opened": "Kansiota ei voitu avata",
    # Server messages
    "Set IMMICH_URL and IMMICH_API_KEY to choose Immich folders, tags and albums.": "Aseta IMMICH_URL ja IMMICH_API_KEY, niin Immich-kansiot, -tunnisteet ja -albumit ovat valittavissa.",
    "Colour-corrected": "Värikorjattu",
    "Invalid group change": "Virheellinen ryhmämuutos",
    "Dye-fade model. Clips the least.": "Väriaineiden haalistumismalli. Vähiten leikkautumista.",
    "Stretch and grey balance. Removes any colour cast.": "Venytys ja harmaatasapaino. Poistaa minkä tahansa värivirheen.",
    "Per-channel stretch only. Keeps the original tone.": "Pelkkä kanavakohtainen venytys. Säilyttää alkuperäisen sävyn.",
    "Unknown correction method": "Tuntematon korjausmenetelmä",
    "Invalid adjustment": "Virheellinen säätö",
    "Adjustment out of range": "Säätö on sallitun alueen ulkopuolella",
    "Unsupported image bit depth": "Kuvan bittisyvyyttä ei tueta",
    "Could not read the JPEG XL container": "JPEG XL -säiliötä ei voitu lukea",
    "possibly dark · check": "mahdollisesti tumma · tarkista",
    "possibly light · check": "mahdollisesti vaalea · tarkista",
    "unusual colours · check": "poikkeavat värit · tarkista",
    "Job not found": "Työtä ei löydy",
    "An export is running": "Vienti on kesken",
    "Top folder": "Pääkansio",
    "All images": "Kaikki kuvat",
    "The source has no images to correct": "Lähteessä ei ole korjattavia kuvia",
    "The source has no images": "Lähteessä ei ole kuvia",
    "Image not found in this job": "Kuvaa ei löydy tästä työstä",
    "Group not found in this job": "Ryhmää ei löydy tästä työstä",
    "Invalid group": "Virheellinen ryhmä",
    "Invalid group id or name": "Virheellinen ryhmän tunnus tai nimi",
    "A job needs at least one group": "Työssä on oltava vähintään yksi ryhmä",
    "The groups were changed on another page. Reload the page before saving.": "Ryhmät ovat muuttuneet toisella sivulla. Päivitä sivu ennen tallennusta.",
    "Invalid image": "Virheellinen kuva",
    "Every image must belong to one group": "Jokaisen kuvan on kuuluttava yhteen ryhmään",
    "The group does not belong to this job": "Ryhmä ei kuulu tähän työhön",
    "Give the group a name (at most 100 characters)": "Anna ryhmälle nimi (enintään 100 merkkiä)",
    "An empty group cannot be approved": "Tyhjää ryhmää ei voi hyväksyä",
    "An export is already running": "Vienti on jo käynnissä",
    "could not read the original's details: {failure}": "alkuperäisen tietoja ei saatu: {failure}",
    "details": "tiedot",
    "album {album}": "albumi {album}",
    "{part} not copied: {failure}": "{part} jäi kopioimatta: {failure}",
    "albums, shared links and stack not moved ({failure}); the old one was not deleted": "albumeja, jakolinkkejä ja pinoa ei siirretty ({failure}); vanhaa ei poistettu",
    "the original is in an external library, so it was not deleted (Immich does not delete its file)": "alkuperäinen on ulkoisessa kirjastossa, joten sitä ei poistettu (Immich ei poista sen tiedostoa)",
    "could not move the original to the trash: {failure}": "alkuperäistä ei saatu roskakoriin: {failure}",
    "earlier copy {asset} not deleted: its details refer to another original": "aiempaa kopiota {asset} ei poistettu: sen tiedot viittaavat toiseen alkuperäiseen",
    "could not take earlier copy {asset} out of the stack: {failure}": "aiempaa kopiota {asset} ei saatu pois pinosta: {failure}",
    "could not move earlier copies {assets} to the trash: {failure}": "aiempia kopioita {assets} ei saatu roskakoriin: {failure}",
    "Immich did not store identifiers in the images' metadata (needs Immich v2.5 or newer): {failure}": "Immich ei tallentanut tunnisteita kuvien metatietoihin (vaatii Immich v2.5:n tai uudemman): {failure}",
    "Immich did not read the copy's details within a minute; its tags may be lost": "Immich ei lukenut kopion tietoja minuutissa; sen tunnisteet voivat kadota",
    "uploaded, but stacking with the original failed: {failure}": "ladattu, mutta pinoaminen alkuperäisen kanssa epäonnistui: {failure}",
    "The destination would replace the original image": "Kohde korvaisi alkuperäisen kuvan",
    "unexpected error {kind}: {failure}": "odottamaton virhe {kind}: {failure}",
    "lossless 16-bit JPEG XL": "häviötön 16-bit JPEG XL",
    "JPEG (small, lossy)": "JPEG (pieni, häviöllinen)",
    "Choose a folder": "Valitse kansio",
    "The folder is outside the allowed roots: {roots}": "Kansio on sallittujen juurien ulkopuolella: {roots}",
    "Folder not found: {path}": "Kansiota ei löydy: {path}",
    "Folder {path}": "Kansio {path}",
    "Folder {path} with subfolders": "Kansio {path} alikansioineen",
    "Immich folder {path}": "Immich-kansio {path}",
    "Immich folder {path} with subfolders": "Immich-kansio {path} alikansioineen",
    "Immich tag {name}": "Immich-tunniste {name}",
    "Immich album {name}": "Immich-albumi {name}",
    "tag {name}": "tunniste {name}",
    "album {name}": "albumi {name}",
    "replaces the original": "korvaa alkuperäisen",
    "stacked with the original": "pinossa alkuperäisen kanssa",
    "the original's albums, tags and details": "alkuperäisen albumit, tunnisteet ja tiedot",
    "earlier copy to the trash": "aiempi kopio roskakoriin",
    "Choose a source": "Valitse lähde",
    "Immich folder not found": "Immich-kansiota ei löydy",
    "Immich tag not found": "Immich-tunnistetta ei löydy",
    "Immich album not found": "Immich-albumia ei löydy",
    "Several albums have that name": "Samannimisiä albumeja on useita",
    "Choose a destination": "Valitse kohde",
    "Unknown file format": "Tuntematon tallennusmuoto",
    "Invalid file name suffix": "Virheellinen nimen pääte",
    "The destination is in the source folder: give a file name suffix so the originals are not replaced": "Kohde on lähdekansiossa: anna nimelle pääte, ettei alkuperäisiä korvata",
    "Give the Immich destination a tag or an album, so the corrected images can be found": "Anna Immich-kohteelle tunniste tai albumi, jotta korjatut kuvat löytyvät",
    "Source image not found: {name}": "Lähdekuvaa ei löydy: {name}",
    "Could not create a preview of the image": "Kuvan esikatselua ei voitu luoda",
    "Immich refused the request {request}: {status} {text}": "Immich hylkäsi pyynnön {request}: {status} {text}",
    "Immich does not respond ({request}, {attempts} attempts): {problem}": "Immich ei vastaa ({request}, {attempts} yritystä): {problem}",
    "Could not get original {asset}: {status}": "Alkuperäistä kuvaa {asset} ei saatu: {status}",
    "the downloaded file does not match Immich's checksum": "ladattu tiedosto ei vastaa Immichin tarkistussummaa",
    "Could not download original {asset} ({attempts} attempts): {problem}": "Alkuperäistä kuvaa {asset} ei saatu ladattua ({attempts} yritystä): {problem}",
    "Immich is not configured: set IMMICH_URL and IMMICH_API_KEY": "Immich ei ole käytössä: aseta IMMICH_URL ja IMMICH_API_KEY",
    # Command line
    "--source: use local:PATH, immich-folder:PATH, immich-tag:TAG or immich-album:NAME": "--source: käytä local:POLKU, immich-folder:POLKU, immich-tag:TUNNISTE tai immich-album:NIMI",
    "No single album is named {name!r}": "Albumia {name!r} ei löydy yksiselitteisesti",
    "--dest: use local:PATH or immich (with --dest-tag / --dest-album)": "--dest: käytä local:POLKU tai immich (ja --dest-tag / --dest-album)",
    "Colour correction for faded photos": "Haalistuneiden valokuvien värikorjaus",
    "start the web UI": "käynnistä selainkäyttöliittymä",
    "correct and export a whole source at once": "korjaa ja vie koko lähde kerralla",
    "include subfolders": "myös alikansiot",
    "file name suffix of a local copy, for example --suffix=-corrected": "paikallisen kopion nimen pääte, esim. --suffix=-korjattu",
    "jxl: lossless 16-bit (default), jpeg: small and lossy": "jxl: häviötön 16-bit (oletus), jpeg: pieni ja häviöllinen",
    "do not stack the Immich copy with the original": "älä pinoa Immich-kopiota alkuperäisen kanssa",
    "replace the Immich original with the corrected copy: details move over, the original goes to the trash": "korvaa Immich-alkuperäinen korjatulla: tiedot siirtyvät, alkuperäinen roskakoriin",
    "move the earlier copy of the same image to Immich's trash": "siirrä saman kuvan aiempi kopio Immichin roskakoriin",
    "do not copy the original's albums, tags and Immich details": "älä kopioi alkuperäisen albumeja, tunnisteita ja Immich-tietoja",
    "archive the originals with their metadata and correction recipes": "arkistoi alkuperäiset metatietoineen ja korjausohjeineen",
    "archive folder": "arkistohakemisto",
    "check the archive's files against SHA256SUMS": "tarkista arkiston tiedostot SHA256SUMS-luetteloa vasten",
    "new": "uusi",
    "{originals} originals: {added} new, {unchanged} already archived; {codes} code versions": "Alkuperäisiä {originals}: uusia {added}, ennestään {unchanged}; koodiversioita {codes}",
    "WARNING: {warning}": "VAROITUS: {warning}",
    "The archive is intact": "Arkisto kunnossa",
    "{count} problems": "{count} ongelmaa",
    "Job {id}: {images} images, {groups} groups": "Työ {id}: {images} kuvaa, {groups} ryhmää",
    "ERROR: {error}": "VIRHE: {error}",
    "Written {written}, skipped {skipped}, errors {errors}": "Kirjoitettu {written}, ohitettu {skipped}, virheitä {errors}",
    # Archive
    "{root} is not an unfade archive ({format})": "{root} ei ole unfade-arkisto ({format})",
    "{root} is neither empty nor an unfade archive": "{root} ei ole tyhjä eikä unfade-arkisto",
    "Archive file {file} is missing or has changed; the archive was not updated": "Arkiston tiedosto {file} puuttuu tai on muuttunut; arkistoa ei päivitetty",
    "{file} exists but is not in the manifest and does not match the original; the archive was not updated": "{file} on olemassa mutta puuttuu luettelosta eikä vastaa alkuperäistä; arkistoa ei päivitetty",
    "{name}: the copy in the archive does not match the source": "{name}: kopio arkistoon ei vastaa lähdettä",
    "{name}: the downloaded file does not match Immich's checksum": "{name}: ladattu tiedosto ei vastaa Immichin tarkistussummaa",
    "The archive and the source cannot be inside one another": "Arkisto ja lähde eivät voi olla sisäkkäin",
    "Code version {id} (commit {commit}) could not be archived": "Koodiversiota {id} (commit {commit}) ei saatu arkistoon",
    "missing: {name}": "puuttuu: {name}",
    "changed: {name}": "muuttunut: {name}",
    "no date": "ei päiväystä",
    "{count} corrected": "{count} korjattua",
    "Tags": "Tunnisteet",
    "Albums": "Albumit",
    "Years": "Vuodet",
    "All": "Kaikki",
    "Archive of originals": "Alkuperäisten arkisto",
    "{count} originals · updated {time}": "{count} alkuperäistä · päivitetty {time}",
    "guide:": "ohje:",
    "check:": "tarkistus:",
    "View": "Näkymä",
}

# The README written into an archive (unfade/archive.py README in English).
FI_ARCHIVE_README = """# Alkuperäisten arkisto (unfade)

Tämä hakemisto on unfaden tekemä arkisto alkuperäisistä kuvista. Se on tarkoitettu
säilymään ilman unfadea, Immichiä tai tätä konetta.

## Sisältö

- `originals/<nimi>` – alkuperäinen kuva tavu tavulta sellaisena kuin Immich (tai lähdekansio)
  sen säilytti. Arkistoa ei koskaan muokkaa tätä tiedostoa.
- `originals/<nimi>.xmp` – XMP-sivutiedosto: kuvaus, tunnisteet (`dc:subject` ja
  `digiKam:TagsList`), arvio, päiväys aikavyöhykkeineen, sijainti, henkilöt kasvoalueineen
  (MWG-alueet), albumit, suosikki- ja roskakoritila sekä jokainen korjattu kopio ohjeineen.
  digiKam, darktable, Lightroom, exiftool ja Immich lukevat sen.
- `originals/<nimi>.json` – täydellinen tietue: tiivisteet (SHA-256, SHA-1 eli Immichin
  tarkistussumma, pikselitiiviste, pHash, dHash), lähteet (Immich-tunnus ja polku), Immichin
  koko kuvatieto sellaisenaan, albumit, kasvot ja korjatut kopiot.
- `code/unfade-<koodin tunnus>/` – se korjauskoodi, jolla kopio tehtiin, tiedostoittain.
  Tunnus on tiedostojen tiiviste, joten kansion sisältö on täsmälleen se koodi.
  `uv.lock` kertoo kirjastojen versiot.
- `code/recompute.py` – laskee kopion uudelleen alkuperäisestä ja ohjeesta.
- `index.html` – selattava yleiskuva tunnisteittain, albumeittain ja vuosittain.
- `manifest.json` – luettelo alkuperäisistä ja niiden pysyvistä nimistä.
- `SHA256SUMS` – jokaisen tiedoston tiiviste.

## Tarkistus

    sha256sum -c SHA256SUMS

## Kopion uudelleenlaskenta

Korjattu kopio ei ole arkistossa, mutta sen voi laskea uudelleen: ohje (menetelmä, sovitetut
käyräparametrit ja kuvan omat säädöt) on alkuperäisen JSON- ja XMP-tiedostossa, ja koodi on
hakemistossa `code/`.

    python3 code/recompute.py originals/<nimi> --out kopio.png
    python3 code/recompute.py originals/<nimi> --copy <UUID> --check <olemassa oleva kopio>

Tarvitaan Python 3 sekä numpy, pillow, pillow-heif ja imagecodecs. Tulos on häviötön
16-bittinen PNG, jonka pikselit ovat samat kuin alkuperäisessä kopiossa.

## Nimet

Nimet päätetään, kun alkuperäinen arkistoidaan ensimmäisen kerran, eivätkä ne muutu.
Immich-lähteen alkuperäiset ovat yhdessä tasaisessa hakemistossa alkuperäisellä nimellään;
paikallisen kansion alikansiot säilyvät. Jos sama nimi on jo toisella sisällöllä, nimeen
lisätään tiedoston SHA-256-tiivisteen alku. Tunnisteet, albumit ja päiväykset ovat
sivutiedostoissa, eivät hakemistorakenteessa.
"""
