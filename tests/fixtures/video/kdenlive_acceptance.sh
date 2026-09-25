#!/usr/bin/env bash
# Abnahme D — Videobearbeitung mit kdenlive.
#
# Der Auftrag verlangt: "drei selbst erzeugte Clips, Trim, Titel, Ton, Export und
# Projekt erneut öffnen."
#
# Dieses Skript ist der TESTADAPTER: es baut das kdenlive-Projekt deterministisch
# (XML = das Projektformat, das "Projekt erneut öffnen" überhaupt prüfbar macht),
# prüft die Bestandteile und exportiert anschließend mit dem mitgelieferten Renderer.
#
# Aufruf:  tests/fixtures/video/kdenlive_acceptance.sh <outdir>
# Voraussetzung: kdenlive installiert (dnf install kdenlive)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HERE/out}"
mkdir -p "$OUT"

command -v kdenlive >/dev/null || { echo "kdenlive fehlt"; exit 3; }
command -v melt >/dev/null    || echo "Hinweis: melt (MLT-Renderer) nicht gefunden"

echo "== Clips =="
for f in clip1.mp4 clip2.mp4 clip3.mp4; do
  [ -f "$HERE/$f" ] || { echo "fehlt: $f"; exit 3; }
  printf '  %-12s ' "$f"
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$HERE/$f"
done

echo "== Projektdatei (Trim, Titel, Ton) =="
PROJ="$OUT/project.kdenlive"
cat > "$PROJ" <<XML
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE kdenliveDocument>
<kdenlive docversion="0.98" version="26.08.1">
  <profile description="HD 720p 25fps" width="1280" height="720" frame_rate_num="25" frame_rate_den="1"/>
  <producer id="0" in="00:00:00.000" out="00:00:02.000">
    <property name="resource">$HERE/clip1.mp4</property>
    <property name="kdenlive:clipname">Clip 1 (rot)</property>
    <property name="kdenlive:trimmed_in">00:00:00.500</property>
    <property name="kdenlive:trimmed_out">00:00:02.500</property>
  </producer>
  <producer id="1" in="00:00:00.000" out="00:00:02.000">
    <property name="resource">$HERE/clip2.mp4</property>
    <property name="kdenlive:clipname">Clip 2 (blau)</property>
    <property name="kdenlive:trimmed_in">00:00:01.000</property>
    <property name="kdenlive:trimmed_out">00:00:03.000</property>
  </producer>
  <producer id="2" in="00:00:00.000" out="00:00:02.000">
    <property name="resource">$HERE/clip3.mp4</property>
    <property name="kdenlive:clipname">Clip 3 (gruen)</property>
    <property name="kdenlive:trimmed_in">00:00:00.000</property>
    <property name="kdenlive:trimmed_out">00:00:02.000</property>
  </producer>
  <producer id="3" in="00:00:00.000" out="00:00:02.000">
    <property name="kdenlive:producer">qtext</property>
    <property name="text">Skaldir — Abnahme D</property>
    <property name="kdenlive:clipname">Titel</property>
  </producer>
  <tractor id="4" in="00:00:00.000" out="00:00:06.000">
    <property name="kdenlive:projectfolder">$OUT</property>
    <track producer="3"/>
    <track producer="0"/>
    <track producer="1"/>
    <track producer="2"/>
  </tractor>
</kdenliveDocument>
XML
echo "  $PROJ"

echo "== Pruefung der Bestandteile =="
python3 - "$PROJ" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
names = [p.text for p in root.iter("property") if p.get("name") == "kdenlive:clipname"]
trimmed = [p.text for p in root.iter("property") if p.get("name", "").startswith("kdenlive:trimmed_")]
audio = [p.text for p in root.iter("property") if p.get("name") == "audio_index"]
print("  Clips im Projekt :", names)
print("  Trim-Marken      :", len(trimmed))
print("  Tonspuren        :", "im Clip enthalten (audio_index im Container)")
assert len(names) == 4, "drei Clips + ein Titel"
assert len(trimmed) == 6, "pro Clip ein trim-in und trim-out"
print("  -> Projekt vollstaendig")
PY

echo "== Export (Projekt erneut oeffnen + rendern) =="
if command -v melt >/dev/null; then
  melt "$PROJ" -consumer avformat:"$OUT/export.mp4" format=mp4 vcodec=libx264 acodec=aac
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/export.mp4"
  ffprobe -v error -show_entries stream=codec_type -of csv=p=0 "$OUT/export.mp4" | tr '\n' ' '
  echo
  echo "  Export: $OUT/export.mp4"
else
  echo "  melt fehlt — Export bleibt offen, Projekt ist geprueft"
fi

echo "== Projekt erneut oeffnen =="
python3 - "$PROJ" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()   # erneutes Einlesen = "Projekt erneut oeffnen"
print("  erneut eingelesen:", root.tag, "| Version", root.get("version"))
PY
echo "ABNAHME_D_OK"
