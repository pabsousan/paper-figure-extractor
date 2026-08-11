# Changelog

## Version 2.3

- SVG files are now created only for true-vector PDF regions.
- Mixed and raster sources produce PNG only, even under the default `both`
  setting.
- An explicit `--format svg` request falls back to PNG when the source is not
  vector and prints a clear explanation.
- Output-folder organization now uses the number of files actually produced
  after vector-quality filtering.

## Version 2.2

- Reads complete figure captions and reports caption-listed panel counts in the
  inventory, including for rasterized figures.
- Opens an interactive drag-to-select crop window when a requested panel letter
  is baked into a raster image instead of selectable PDF text.
- Automatically creates a `FirstAuthor_Year_figures` subfolder whenever the
  requested extraction will generate multiple files.
- Validated against Pai et al. (2026), including rasterized Figures 1, 2, and 5.

## Version 2.1

- Guided mode now opens the operating system's folder-selection window before
  extraction.
- Adds `--choose-output` for the same behavior in command-line workflows.
- Falls back to a typed destination path if the graphical picker is unavailable
  or cancelled in guided mode.

## Version 2

- Creates SVGs from a tightly cropped temporary PDF page instead of only
  changing the original page viewport.
- Reports whether each source region is true vector, mixed, or raster-backed.
- Supports panel requests such as `Figure 1A` and `Figure S5C` when panel
  letters exist as PDF text.
- Detects regular panel grids and exports the requested cell only.
- Automatically names outputs using DOI/Crossref metadata, then PDF metadata,
  then the PDF filename.
- Adds `--offline` and retains `--paper-name` as an optional override.
