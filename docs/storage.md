# Separater Dateispeicher ab Chart 0.2.0

Ein vorbereitetes zusätzliches PVC kann die drei Verzeichnisse `depot`,
`repository` und `workbench` aufnehmen. Die Mounts liegen unter `/data/lib`,
damit der unveränderte Upstream-Start `/var/lib/opsi` weiterhin korrekt bindet.
Konfiguration, Serveridentität, private Schlüssel, Laufzeitdaten und TFTP bleiben
auf den bisherigen Volumes. MariaDB und Valkey sind unabhängig davon.

Bei einem NAS mit Benutzer-Mapping können `mappedUid` und `mappedGid` die lokalen
IDs von `opsiconfd` und `opsifileadmins` an die **vorher gemessenen** Export-IDs
angleichen. Das Chart ändert keine NAS-Einstellungen und unterdrückt keine
Rechtefehler. Native OPSI-Rechteprüfungen einschließlich Paketinstallation bleiben
aktiv. Der Standard ohne `fileStorage` ändert keine Benutzer-IDs.

```yaml
fileStorage:
  enabled: true
  existingClaim: opsi-files-nfs
  mappedUid: 977
  mappedGid: 988
```

Diese IDs sind ein Beispiel für den geprüften UniFi-Export, kein allgemeiner
NFS-Standard. Beide Werte zusammen setzen; ohne Mapping beide weglassen.
Die Standardkonten `opsiconfd`, `pcpatch` und `opsifileadmins` werden erwartet;
Kollisionen mit anderen Konten führen vor der Umstellung zum Abbruch. Primäre
Mitglieder der Dateigruppe werden mitgeführt. Weitere administrative Nutzer
in dieser primären Gruppe erfordern eine gesonderte Migration.

## Daten vorbereiten

1. Test- und Produktionspfade strikt trennen. OPSI anhalten, damit niemand die
   drei Dateibäume verändert. Die bisherigen PVCs und einen konsistenten
   Sicherungsstand für einen möglichen Rückweg behalten.
2. In einem temporären Wartungs-Pod das bisherige Daten-PVC **read-only** nach
   `/source` und den vorgesehenen Export nach `/nas` mounten. Nicht das gesamte
   `/data` auf einen Export mit zusammengeführten Unix-Identitäten kopieren.
3. Das mitgelieferte Skript ausführen, beispielsweise:

   ```sh
   python3 prepare-file-storage.py --source /source/lib \
     --target /nas/test/files --host-id opsi-test.example.org --uid 977 --gid 988
   ```

   Das Ziel darf noch nicht existieren. Das Skript kopiert ausschließlich die
   drei Verzeichnisse, erhält Symlinks und Modi ohne fremde Eigentümer zu setzen,
   prüft Inhalte per SHA-256 und schreibt erst danach den Marker
   `.opsi-file-storage.json`. Angefangene eigene Kopien werden bei Fehlern entfernt.
   Der Operator muss weiterhin sicherstellen, dass die Quelle angehalten ist.
   Für eine neue leere Installation kann die unveränderte Image-Quelle
   `/var/lib/opsi` im Wartungs-Pod statt eines bestehenden Daten-PVCs dienen.
4. Ein separates gehaltenes PV/PVC auf genau diesen neuen `files`-Pfad binden und
   `fileStorage` aktivieren. Identitäts- und TFTP-PVCs unverändert referenzieren.

Beim Start prüft das Chart den vorbereiteten Marker samt Servernamen, vorhandene
Eigentümer sowie Schreiben, Modus `2770`, ausführbare Dateien, gruppenbezogenen
`chown`, atomare Umbenennung und `fsync` mit dem tatsächlichen Dienstbenutzer.
Es legt keine leeren Ersatzdepots an und überschreibt keinen vorhandenen Bestand.

Danach Neustart, WebDAV, einen Paketimport und PXE mit Hardwareinventarisierung
prüfen. Nach ersten Änderungen auf NFS ist ein Rückweg zum alten PVC nur mit
einem erneuten konsistenten Dateiabgleich möglich; die verdeckten alten Dateien
werden nicht automatisch aktuell gehalten. Bei einer Rückkehr zum alten Chart
korrigiert dessen natives `opsi-set-rights` die lokalen IDs erneut.

Ein Export, der alle Unix-Benutzer zusammenführt, ersetzt keine Zugriffstrennung.
Deshalb liegen insbesondere die privaten CA-Schlüssel außerhalb dieses Exports.
Kubernetes-PV-Größen richten keine Quoten auf statischen NFS-Verzeichnissen ein.
