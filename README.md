# Paper Figure Extractor

Extract full figures, individual figure panels, and tables from scholarly PDFs as:

- **SVG**: created only when the selected PDF region is genuinely vector.
- **600-DPI PNG**: a sharp, broadly compatible image.

The default `both` setting is quality-aware: true-vector regions produce SVG
and PNG, while mixed or raster regions produce PNG only. This prevents a raster
image wrapped in an SVG container from being mistaken for scalable artwork.

The tool detects captions such as `Figure 1`, `Figure S5`, and `Table 2`. You
can extract everything, select labels, select an individual panel such as
`Figure 1A`, or select items by their order of appearance.

The inventory shows the caption-listed panel count before you choose anything,
for example `panels A-C (3)`. This helps prevent requesting a panel that does
not exist.

It also looks for a DOI and queries Crossref to name outputs automatically as
`FirstAuthor_Year`. If the lookup is unavailable, it falls back to the PDF's
author/date metadata and then its filename. `--paper-name` is retained only as
an optional override.

## Easiest use on Windows

1. Install [Python 3](https://www.python.org/downloads/) if it is not installed.
   During installation, enable **Add Python to PATH**.
2. Double-click `run_extractor.bat`.
3. Paste or type the PDF path and follow the prompts.
4. Choose the destination folder in the File Explorer window that opens.

The first launch installs the one required PDF library in a private `.venv`
folder. Later launches start directly.

By default, results go beside the PDF in a folder such as
`Segretin_2026_figures`, with names such as:

```text
Segretin_2026_Fig.1.svg
Segretin_2026_Fig.1.png
Segretin_2026_Fig.1A.svg
Segretin_2026_Fig.1A.png
Segretin_2026_Table_2.svg
Segretin_2026_Table_2.png
```

When the extraction will create more than one file, the tool automatically
creates a `FirstAuthor_Year_figures` subfolder inside the destination you chose.
For example, selecting Downloads creates `Downloads/Pai_2026_figures` instead
of placing several loose files directly in Downloads.

## Command-line use

Install once:

```console
python -m pip install -r requirements.txt
```

Guided mode:

```console
python extract_figures.py
```

List what the extractor detects:

```console
python extract_figures.py paper.pdf --list
```

Extract all detected figures and tables:

```console
python extract_figures.py paper.pdf --all
```

Extract by caption label:

```console
python extract_figures.py paper.pdf --select "Figure 1" "Figure 1A" "Figure S5" "Table 2"
```

Extract the first, third, and sixth detected items:

```console
python extract_figures.py paper.pdf --order 1 3 6
```

Useful options:

```text
--format both|svg|png    Requested formats (SVG is emitted only for true vectors)
--dpi 600               PNG resolution
--include-caption       Include caption text in the image
--output-dir PATH       Choose the destination folder
--choose-output         Open a File Explorer folder-selection window
--margin 6              Padding around each crop, in PDF points
--offline               Skip the DOI/Crossref lookup
--paper-name NAME       Override automatic FirstAuthor_Year detection
```

Run `python extract_figures.py --help` for the complete reference.

To use the folder picker while otherwise supplying command-line arguments:

```console
python extract_figures.py paper.pdf --select "Figure 1A" --choose-output
```

## Difficult layouts and manual crops

PDFs do not contain a universal "this rectangle is Figure 1" marker. Most
publisher layouts work automatically, but a scan, unusual caption placement,
or complex multi-column layout may need a manual crop.

PDF coordinates use points (72 points per inch), measured from the top-left.
The following extracts a rectangle from page 4 and calls it `Fig.3`:

```console
python extract_figures.py paper.pdf --crop "4,50,90,545,410,Fig.3"
```

Manual crops can be repeated. They are also useful when a PDF is scanned and
has no searchable caption text.

## Individual panels

Request a panel by appending its letter to the figure number:

```console
python extract_figures.py paper.pdf --select "Figure 1A" "Figure 1C"
```

The script first tries to detect panel letters (`A`, `B`, `C`, etc.) as PDF text
and crop the panel automatically. When a publisher has baked those letters into
a raster image, the caption is used to verify the panel count and a graphical
crop window opens. Drag a rectangle around the requested panel and click
**Use selection**. This avoids both an extraction failure and an unsafe guessed
crop.

## SVG and source quality

- The SVG is generated from a new, tightly cropped PDF page. If the original
  plot consists of PDF paths, the result is a genuine vector SVG and scales
  without losing resolution.
- If the publisher stored a figure as JPEG/PNG pixels, an SVG can only contain
  that same raster image. The extractor therefore skips SVG for raster and
  mixed sources and writes PNG instead. It reports every result as **true
  vector**, **mixed vector/raster**, or **raster source** in the terminal.
- If `--format svg` is explicitly requested for a non-vector source, the tool
  saves a PNG instead and explains the substitution in the terminal.
- PNG output is rendered directly from the PDF at 600 DPI. Raising the DPI can
  improve vector rendering but cannot restore detail removed by the publisher.
- Some publishers downsample images before placing them in a PDF. In that case,
  no PDF extractor can recover the pre-publication source resolution.
- The extractor does not use OCR. Scanned PDFs can still be handled with a
  manual crop.

## Automatic paper names

The automatic naming order is:

1. DOI in the PDF -> Crossref first-author surname and publication year.
2. PDF `Author` and creation-date metadata.
3. Existing PDF filename.

The Crossref REST API does not require an account. The lookup needs an internet
connection and sends only the DOI found in the paper. Use `--offline` to disable
it. The detected source is always printed so it can be checked.
