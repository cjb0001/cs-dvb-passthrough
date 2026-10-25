"""Keep the single-file utility's shared helpers identical to the core modules."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
script = ROOT / "dvb-passthrough.sh"
text = script.read_text(encoding="utf-8")
for label, filename, first_function in (
    ("physical tuner selection", "dvb-selection.func", "dvb_selection_error()"),
    ("DVB banner helpers", "dvb-banner.func", "dvb_banner_profile()"),
    ("container permissions", "dvb.func", "setup_dvb()"),
):
    library = (ROOT / "community-scripts/core/lib" / filename).read_text(encoding="utf-8")
    body = library[library.index(first_function):]
    start, end = f"# BEGIN shared {label}\n", f"# END shared {label}\n"
    block = start + body + end
    if start in text:
        before, remaining = text.split(start, 1)
        _, after = remaining.split(end, 1)
        text = before + block + after
    else:
        text = text.replace("\nDRY_RUN=false\n", "\n" + block + "\nDRY_RUN=false\n", 1)
with script.open("w", encoding="utf-8", newline="\n") as stream:
    stream.write(text)
