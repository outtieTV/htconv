# HTconv — HTS / HTC Texture Converter

A Python 3 utility for converting, extracting, and packing `.hts` and `.htc` texture archives.

HTconv supports converting between HTS and HTC formats, exporting supported textures to PNG images, and importing PNG images into archives using a specified texture checksum.

## Features

- Convert `.hts` archives to `.htc`.
- Convert `.htc` archives to `.hts`.
- Extract supported textures from either format into PNG files.
- Pack PNG textures into an archive using their checksums.
- Replace existing textures by checksum.
- Export archive metadata for inspection.
- Preserve existing texture metadata and payloads when converting archives.

## Requirements

- Python 3.9 or newer.
- [Pillow](https://pypi.org/project/Pillow/) for PNG import and export.

Install Pillow using:

```powershell
python -m pip install Pillow
```

On Windows, you can also use the Python launcher:

```powershell
py -m pip install Pillow
```

No additional packages are required for HTS/HTC conversion without PNG operations.

## Installation

1. Download or clone this repository.
2. Save `HTconv.py` in your preferred directory.
3. Install Pillow if you intend to extract or import PNG textures.
4. Open a terminal in the directory containing the script.

Example:

```powershell
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Replace `YOUR_USERNAME/YOUR_REPOSITORY` with your GitHub repository path.

## Usage

The general command syntax is:

```text
python HTconv.py --open INPUT_FILE [OPTIONS]
```

On Windows, you can use `py` instead of `python`.

### Convert HTS to HTC

Convert an existing HTS archive into HTC format:

```powershell
python HTconv.py --open "textures.hts" --save "textures.htc"
```

This reads the HTS archive and writes an HTC archive containing the same textures.

### Convert HTC to HTS

Convert an existing HTC archive into HTS format:

```powershell
python HTconv.py --open "textures.htc" --save "textures.hts"
```

This reads the HTC archive and writes an HTS archive with an index table.

**Note:** The output extension determines which archive format is written. Use `.hts` or `.htc` for the output filename.

## Extract textures

HTconv can export supported textures from either HTS or HTC archives to PNG images.

### Extract an HTS archive

```powershell
python HTconv.py --open "textures.hts" --export "exported"
```

### Extract an HTC archive

```powershell
python HTconv.py --open "textures.htc" --export "exported"
```

The output directory is created automatically if it does not exist.

Exported PNG files use the texture checksum in their filenames. For example:

```text
exported/
├── #12345678#.png
├── #abcdef01#.png
└── #1234abcd#.png
```

Only RGBA8 textures are currently exported to PNG. Other texture formats are skipped with a warning.

## Pack textures

Packing means importing PNG files into an archive and saving the resulting archive.

Each imported texture needs a checksum. The checksum identifies the texture entry in the archive.

### Create an HTS archive with a new texture

```powershell
python HTconv.py --open "textures.hts" --insert "new_texture.png" 0x12345678 --save "packed.hts"
```

### Create an HTC archive with a new texture

```powershell
python HTconv.py --open "textures.htc" --insert "new_texture.png" 0x12345678 --save "packed.htc"
```

The examples above insert a new PNG texture with checksum `0x12345678`, then save the archive in the selected format.

The input archive must already exist. To start from an existing archive and add textures, open that archive, insert the textures, and save to a new output filename.

### Insert multiple textures

You can insert multiple textures by repeating the `--insert` option:

```powershell
python HTconv.py --open "textures.hts" --insert "texture1.png" 0x12345678 --insert "texture2.png" 0xabcdef01 --save "packed.hts"
```

Each PNG needs its own checksum.

### Replace an existing texture

To replace an existing texture while keeping its checksum:

```powershell
python HTconv.py --open "textures.hts" --replace "replacement.png" 0x12345678 --save "replaced.hts"
```

For HTC:

```powershell
python HTconv.py --open "textures.htc" --replace "replacement.png" 0x12345678 --save "replaced.htc"
```

The specified checksum must already exist in the input archive.

### Checksum formats

Checksums can be supplied in decimal or hexadecimal notation.

Examples:

```text
0x12345678
abcdef01
12345678
```

A value containing hexadecimal letters is interpreted as hexadecimal. A value containing only digits is interpreted as decimal unless prefixed with `0x`.

## Dump archive metadata

To inspect an archive and write its texture metadata to `dump.txt`:

```powershell
python HTconv.py --open "textures.hts" --dump
```

For HTC:

```powershell
python HTconv.py --open "textures.htc" --dump
```

Specify a different output filename if desired:

```powershell
python HTconv.py --open "textures.hts" --dump "metadata.txt"
```

The dump includes:

- Texture checksum
- Texture dimensions
- Texture format
- Pixel type
- High-resolution flag
- Stored payload size
- Compression status
- Original texture location, when available

## Command reference

| Option | Description |
|---|---|
| `--open FILE` | Open an HTS or HTC archive. |
| `--save FILE` | Save the archive as HTS or HTC, based on the output extension. |
| `--export DIR` | Export supported textures as PNG images. |
| `--insert PNG CHECKSUM` | Insert a new texture using the supplied checksum. |
| `--replace PNG CHECKSUM` | Replace an existing texture by checksum. |
| `--dump [FILE]` | Export archive metadata. Defaults to `dump.txt`. |
| `--help` | Display command-line help. |

The original single-dash forms are also supported, such as `-open`, `-save`, `-export`, `-insert`, `-replace`, and `-dump`.

## Troubleshooting

### Pillow is missing

Install it with:

```powershell
python -m pip install Pillow
```

### The checksum already exists

Use `--replace` to replace an existing texture, or choose a different checksum for a new texture.

### The checksum does not exist

`--replace` requires a checksum already present in the archive. Use `--dump` to inspect the archive's checksums.

### Some textures are skipped during extraction

Only RGBA8 textures are currently exported to PNG. Unsupported formats are skipped with a warning rather than exported incorrectly.

### The archive cannot be opened

Verify that the file is a valid HTS or HTC archive and that it matches the supported layout. Keep a backup of the original file before modifying or converting archives.

## Limitations

- PNG import and export currently support RGBA8 textures.
- Importing a PNG creates an RGBA8 texture entry.
- HTconv implements the HTC writer independently because the supplied C++ implementation's HTC save routine is unfinished.
- Compatibility with every variation of HTS/HTC archives has not been established.

## Credits

Based on the HTS/HTC archive format and C++ source code from [DaMarkov/HTconv](https://github.com/DaMarkov/HTconv/).

This Python implementation is a separate implementation based on the original project's source code.

## License

Add the license applicable to your project here. If you distribute or adapt code from another project, review its license and attribution requirements before publishing.
