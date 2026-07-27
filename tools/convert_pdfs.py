#!/usr/bin/env python3
"""
Batch-convert PDFs into Obsidian Markdown notes that follow the vault's
frontmatter contract (see CLAUDE.md).

For each PDF it:
  1. converts the text to Markdown (markitdown -> pdftotext fallback),
  2. copies the original PDF into 99-Meta/Attachments (kept as the source of truth),
  3. writes a .md note with the correct frontmatter for its `type`,
  4. files it into the right folder for its course/type.

Usage (via the convert.cmd wrapper, so you don't type the venv path):
  convert --source "C:\\path\\to\\pdfs" --course CSE-421 --type lecture
  convert --source "one-file.pdf" --course PHYS-121 --type exam-prep
  convert --source "C:\\readings" --type resource            (no course needed)

Options:
  --source       Folder of PDFs, or a single .pdf file            (required)
  --type         lecture | assignment | exam-prep | resource      (required)
  --course       e.g. CSE-421   (required for course types)
  --vault        Vault root (default: the configured path below)
  --recursive    Recurse into sub-folders of --source
  --overwrite    Replace existing .md notes (default: skip them)
  --no-keep-pdf  Don't copy the original PDF into Attachments
"""
import argparse, re, shutil, subprocess, sys
from pathlib import Path

DEFAULT_VAULT = Path(r"C:\Users\tusha\documents\obsidian vault")

# type -> (destination folder relative to vault, needs a course?, tag)
TYPES = {
    "lecture":    ("02-Areas/Academics/{course}/lectures",    True,  "lecture"),
    "assignment": ("02-Areas/Academics/{course}/assignments", True,  "assignment"),
    "exam-prep":  ("02-Areas/Academics/{course}/exams",       True,  "exam"),
    "resource":   ("04-Resources",                            False, "resource"),
}


def kebab(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return re.sub(r"-{2,}", "-", name) or "untitled"


def title_from(stem: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", stem)).strip().title()


def frontmatter(note_type: str, course: str, tag: str) -> str:
    if note_type == "lecture":
        body = f"type: lecture\ncourse: {course}\nnumber: \ndate: "
    elif note_type == "assignment":
        body = f"type: assignment\ncourse: {course}\nstatus: todo\nassigned: \ndue: \ngrade: "
    elif note_type == "exam-prep":
        body = f"type: exam-prep\ncourse: {course}\nexam: \ndate: \nstatus: studying"
    else:  # resource
        body = f"type: resource\ncourse: {course or ''}"
    return f"---\n{body}\ntags: [{tag}]\n---\n"


def convert_text(pdf: Path) -> str:
    """markitdown first; fall back to pdftotext -layout."""
    try:
        from markitdown import MarkItDown
        res = MarkItDown().convert(str(pdf))
        text = getattr(res, "markdown", None) or getattr(res, "text_content", "")
        if text and text.strip():
            return text.strip()
    except Exception as e:
        print(f"    markitdown failed ({e}); trying pdftotext", file=sys.stderr)
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return (out.stdout or "").strip()
    except FileNotFoundError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert PDFs into Obsidian notes.")
    ap.add_argument("--source", required=True)
    ap.add_argument("--type", required=True, choices=list(TYPES))
    ap.add_argument("--course", default="")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--no-keep-pdf", dest="keep_pdf", action="store_false")
    args = ap.parse_args()

    vault = Path(args.vault)
    dest_tmpl, needs_course, tag = TYPES[args.type]
    if needs_course and not args.course:
        ap.error(f"--course is required for type '{args.type}'")

    dest_dir = vault / dest_tmpl.format(course=args.course)
    dest_dir.mkdir(parents=True, exist_ok=True)
    attach = vault / "99-Meta" / "Attachments"
    attach.mkdir(parents=True, exist_ok=True)

    src = Path(args.source)
    if src.is_file():
        pdfs = [src]
    else:
        pdfs = sorted((src.rglob if args.recursive else src.glob)("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {src}")
        return 1

    made = skipped = failed = 0
    for pdf in pdfs:
        out = dest_dir / (kebab(pdf.stem) + ".md")
        if out.exists() and not args.overwrite:
            print(f"  skip (exists): {out.name}")
            skipped += 1
            continue
        print(f"  converting: {pdf.name}")
        text = convert_text(pdf)
        if not text:
            print(f"    FAILED to extract text: {pdf.name}", file=sys.stderr)
            failed += 1
            continue

        embed = ""
        if args.keep_pdf:
            target = attach / pdf.name
            if not target.exists():
                shutil.copy2(pdf, target)
            embed = f"> \U0001F4C4 Source PDF: ![[{pdf.name}]]\n\n"

        note = (
            frontmatter(args.type, args.course, tag)
            + f"\n# {title_from(pdf.stem)}\n\n"
            + embed
            + text + "\n"
        )
        out.write_text(note, encoding="utf-8")
        print(f"    -> {out.relative_to(vault)}")
        made += 1

    print(f"\nDone. {made} created, {skipped} skipped, {failed} failed. -> {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
