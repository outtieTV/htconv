#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HTconv.py - HTS / HTC / PNG converter
======================================

Python 3 port based on the supplied DaMarkov HTconv C++ source.

Features:
    * Read .hts archives.
    * Read gzip-compressed .htc archives.
    * Convert .hts <-> .htc.
    * Export RGBA8 textures to PNG.
    * Insert or replace textures from PNG files.
    * Dump archive metadata.
    * Preserve texture checksums and metadata.
    * Validate archive indexes, offsets, and payload sizes.

Dependency:
    py -m pip install Pillow

Examples:
    py HTconv.py --open textures.hts --dump
    py HTconv.py --open textures.hts --export exported
    py HTconv.py --open textures.hts --save textures.htc
    py HTconv.py --open textures.htc --save textures.hts

    py HTconv.py --open textures.hts --insert new.png 0x123456 --save out.hts
    py HTconv.py --open textures.hts --replace replacement.png 0x123456 --save out.hts

Format details derived from ht.h, hts.h, and htc.h:
    m_Config       : signed 32-bit integer
    m_storagePos   : signed 64-bit integer
    checksum       : unsigned 64-bit integer
    texture format : unsigned 16-bit integer
    pixel type     : unsigned 16-bit integer
    GL_TEXFMT_GZ   : 0x80000000
    GL_TEXFMT_RGBA8: 0x00008058

Important:
    The supplied source's HTC writer is unfinished. This script
    implements the sequential HTC writer independently.

    Back up original archives before modifying them.
"""

import argparse
import gzip
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None


# ---------------------------------------------------------------------
# Binary format definitions
# ---------------------------------------------------------------------

CONFIG = struct.Struct("<i")
POINTER = struct.Struct("<q")
COUNT = struct.Struct("<i")
INDEX_ENTRY = struct.Struct("<Qq")
CHECKSUM = struct.Struct("<Q")

# int width, int height, unsigned int format,
# unsigned short texture_format, unsigned short pixel_type,
# unsigned char is_hires_tex, uint32 rawSize.
#
# The supplied HT headers define texture_format and pixel_type as
# unsigned short, so the texture header is 21 bytes, not 25 bytes.
TEXTURE_HEADER = struct.Struct("<iiIHHBI")

CONFIG_SIZE = CONFIG.size                  # 4
HEADER_SIZE = CONFIG_SIZE + POINTER.size    # 12
TEXTURE_HEADER_SIZE = TEXTURE_HEADER.size   # 21
INDEX_ENTRY_SIZE = INDEX_ENTRY.size         # 16

GL_TEXFMT_GZ = 0x80000000
GL_TEXFMT_RGBA8 = 0x00008058
GL_TEXFMT_RGB8 = 0x00008051
GL_TEXFMT_RGBA4 = 0x00008056
GZ_TEXFMT_INDEX8 = 0x000080E5

GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401

MAX_TEXTURE_SIZE = 512 * 1024 * 1024


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass
class Texture:
    checksum: int
    width: int
    height: int
    fmt: int
    texture_format: int
    pixel_type: int
    is_hires: int
    data: bytes
    source_location: int = -1

    @property
    def compressed(self):
        return bool(self.fmt & GL_TEXFMT_GZ)

    @property
    def pixel_format(self):
        return self.fmt & ~GL_TEXFMT_GZ


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def log(message):
    print(message)


def warn(message):
    print(f"[WARNING] {message}", file=sys.stderr)


def error(message):
    raise RuntimeError(message)


def read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        error(
            f"Unexpected end of file: expected {size} bytes, "
            f"received {len(data)}."
        )
    return data


def parse_checksum(value):
    """
    Parse a checksum as decimal or hexadecimal.

    Hexadecimal examples:
        0x12345678
        a1b2c3d4
        00000000abcdef01

    Decimal example:
        12345678
    """
    value = str(value).strip()

    if value.lower().startswith("0x"):
        result = int(value, 16)
    elif any(char in "abcdefABCDEF" for char in value):
        result = int(value, 16)
    else:
        result = int(value, 10)

    if not 0 <= result <= 0xFFFFFFFFFFFFFFFF:
        error("Checksum must fit in an unsigned 64-bit integer.")

    return result


def decode_pixels(texture):
    """Decompress texture payload when GL_TEXFMT_GZ is set."""
    if texture.compressed:
        try:
            return zlib.decompress(texture.data)
        except zlib.error as exc:
            error(
                f"Texture 0x{texture.checksum:016x} "
                f"could not be decompressed: {exc}"
            )

    return texture.data


def encode_pixels(texture, pixels, compress=True):
    """
    Store RGBA pixels in the format used by HTconv.

    This sets GL_TEXFMT_GZ only when the data is actually zlib-
    compressed. The original addImage() implementation defaults
    ZipCompress to false and does not compress its pixel data.
    """
    texture.fmt &= ~GL_TEXFMT_GZ

    if compress:
        texture.data = zlib.compress(pixels)
        texture.fmt |= GL_TEXFMT_GZ
    else:
        texture.data = pixels


def read_texture_header(stream):
    raw = read_exact(stream, TEXTURE_HEADER_SIZE)

    (
        width,
        height,
        fmt,
        texture_format,
        pixel_type,
        is_hires,
        raw_size,
    ) = TEXTURE_HEADER.unpack(raw)

    if width <= 0 or height <= 0:
        error(f"Invalid texture dimensions: {width} x {height}")

    if raw_size <= 0:
        error(f"Invalid texture payload size: {raw_size}")

    if raw_size > MAX_TEXTURE_SIZE:
        error(
            f"Texture payload is too large: {raw_size:,} bytes "
            f"(limit {MAX_TEXTURE_SIZE:,})."
        )

    return (
        width,
        height,
        fmt,
        texture_format,
        pixel_type,
        is_hires,
        raw_size,
    )


def read_texture_record(stream, checksum, location=-1):
    (
        width,
        height,
        fmt,
        texture_format,
        pixel_type,
        is_hires,
        raw_size,
    ) = read_texture_header(stream)

    data = read_exact(stream, raw_size)

    return Texture(
        checksum=checksum,
        width=width,
        height=height,
        fmt=fmt,
        texture_format=texture_format,
        pixel_type=pixel_type,
        is_hires=is_hires,
        data=data,
        source_location=location,
    )


def write_texture_record(stream, texture, include_checksum=False):
    if include_checksum:
        stream.write(CHECKSUM.pack(texture.checksum))

    if not 0 < texture.width <= 0x7FFFFFFF:
        error(f"Invalid texture width: {texture.width}")

    if not 0 < texture.height <= 0x7FFFFFFF:
        error(f"Invalid texture height: {texture.height}")

    if not texture.data:
        error(f"Texture 0x{texture.checksum:016x} has no data.")

    if len(texture.data) > MAX_TEXTURE_SIZE:
        error(f"Texture 0x{texture.checksum:016x} is too large.")

    stream.write(
        TEXTURE_HEADER.pack(
            texture.width,
            texture.height,
            texture.fmt,
            texture.texture_format,
            texture.pixel_type,
            texture.is_hires & 0xFF,
            len(texture.data),
        )
    )
    stream.write(texture.data)


# ---------------------------------------------------------------------
# Archive implementation
# ---------------------------------------------------------------------

class HTArchive:
    def __init__(self, filename):
        self.filename = Path(filename)
        self.config = 0
        self.textures = {}
        self.archive_type = None

        if not self.filename.is_file():
            error(f"File does not exist: {self.filename}")

        suffix = self.filename.suffix.lower()

        if suffix == ".hts":
            self.read_hts()
        elif suffix == ".htc":
            self.read_htc()
        else:
            error("Input filename must end with .hts or .htc.")

        log(
            f"Opened {self.filename}: "
            f"{len(self.textures):,} texture(s), "
            f"{self.archive_type.upper()} format."
        )

    # -----------------------------------------------------------------
    # HTS reader
    # -----------------------------------------------------------------

    def read_hts(self):
        self.textures.clear()

        with self.filename.open("rb") as stream:
            file_size = os.fstat(stream.fileno()).st_size

            if file_size < HEADER_SIZE:
                error("File is too small to contain an HTS header.")

            self.config = CONFIG.unpack(
                read_exact(stream, CONFIG_SIZE)
            )[0]

            storage_pos = POINTER.unpack(
                read_exact(stream, POINTER.size)
            )[0]

            if storage_pos < HEADER_SIZE:
                error(f"Invalid HTS index offset: {storage_pos}")

            if storage_pos + COUNT.size > file_size:
                error(
                    f"HTS index offset {storage_pos} "
                    "is outside the file."
                )

            stream.seek(storage_pos)
            entry_count = COUNT.unpack(
                read_exact(stream, COUNT.size)
            )[0]

            if entry_count <= 0:
                error(f"Invalid HTS index entry count: {entry_count}")

            maximum_entries = (
                file_size - storage_pos - COUNT.size
            ) // INDEX_ENTRY_SIZE

            if entry_count > maximum_entries:
                error(
                    f"HTS index says {entry_count:,} entries, but "
                    f"only {maximum_entries:,} entries fit."
                )

            entries = []

            for _ in range(entry_count):
                checksum, location = INDEX_ENTRY.unpack(
                    read_exact(stream, INDEX_ENTRY_SIZE)
                )

                if location < HEADER_SIZE:
                    warn(
                        f"Ignoring checksum 0x{checksum:016x}: "
                        f"invalid location {location}."
                    )
                    continue

                if location + TEXTURE_HEADER_SIZE > storage_pos:
                    warn(
                        f"Ignoring checksum 0x{checksum:016x}: "
                        "texture header overlaps the index."
                    )
                    continue

                entries.append((checksum, location))

            for checksum, location in entries:
                try:
                    stream.seek(location)

                    texture = read_texture_record(
                        stream,
                        checksum,
                        location,
                    )

                    end = (
                        location
                        + TEXTURE_HEADER_SIZE
                        + len(texture.data)
                    )

                    if end > storage_pos:
                        warn(
                            f"Texture 0x{checksum:016x} overlaps "
                            "the HTS index; skipping."
                        )
                        continue

                    if checksum in self.textures:
                        warn(
                            f"Duplicate checksum 0x{checksum:016x}; "
                            "keeping the first."
                        )
                        continue

                    self.textures[checksum] = texture

                except (RuntimeError, struct.error, OSError) as exc:
                    warn(
                        f"Could not read texture 0x{checksum:016x} "
                        f"at offset {location}: {exc}"
                    )

        self.archive_type = "hts"

        if not self.textures:
            error(
                "No valid textures were found. Verify that the archive "
                "uses the HTS layout defined by the supplied source."
            )

    # -----------------------------------------------------------------
    # HTC reader
    # -----------------------------------------------------------------

    def read_htc(self):
        self.textures.clear()

        try:
            with gzip.open(self.filename, "rb") as stream:
                self.config = CONFIG.unpack(
                    read_exact(stream, CONFIG_SIZE)
                )[0]

                while True:
                    checksum_bytes = stream.read(CHECKSUM.size)

                    if not checksum_bytes:
                        break

                    if len(checksum_bytes) != CHECKSUM.size:
                        error("Truncated HTC checksum at end of file.")

                    checksum = CHECKSUM.unpack(checksum_bytes)[0]

                    texture = read_texture_record(
                        stream,
                        checksum,
                    )

                    if checksum in self.textures:
                        warn(
                            f"Duplicate HTC checksum "
                            f"0x{checksum:016x}; keeping the first."
                        )
                    else:
                        self.textures[checksum] = texture

        except (OSError, EOFError, zlib.error) as exc:
            error(f"Could not read gzip-compressed HTC: {exc}")

        self.archive_type = "htc"

        if not self.textures:
            error(
                "No textures found in the HTC archive. "
                "The archive may use another variant of the format."
            )

    # -----------------------------------------------------------------
    # PNG export
    # -----------------------------------------------------------------

    def export_png(self, folder):
        if Image is None:
            error(
                "Pillow is required for PNG operations.\n"
                "Install it with: py -m pip install Pillow"
            )

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)

        total = len(self.textures)
        exported = 0

        for index, (checksum, texture) in enumerate(
            self.textures.items(), start=1
        ):
            try:
                if texture.pixel_format != GL_TEXFMT_RGBA8:
                    warn(
                        f"Skipping 0x{checksum:016x}: unsupported "
                        f"format 0x{texture.pixel_format:08x}. "
                        "Only RGBA8 can be exported to PNG."
                    )
                    continue

                pixels = decode_pixels(texture)
                expected_size = texture.width * texture.height * 4

                if len(pixels) != expected_size:
                    warn(
                        f"Skipping 0x{checksum:016x}: expected "
                        f"{expected_size:,} RGBA bytes, got "
                        f"{len(pixels):,}."
                    )
                    continue

                image = Image.frombytes(
                    "RGBA",
                    (texture.width, texture.height),
                    pixels,
                )

                output = folder / f"#{checksum:x}#.png"
                image.save(output, format="PNG")

                exported += 1
                print(
                    f"[{index}/{total}] "
                    f"{texture.width}x{texture.height} -> {output}"
                )

            except Exception as exc:
                warn(f"Export failed for 0x{checksum:016x}: {exc}")

        log(f"Exported {exported:,} of {total:,} texture(s).")

    # -----------------------------------------------------------------
    # PNG import / replacement
    # -----------------------------------------------------------------

    def import_png(self, filename, checksum, replace=False):
        if Image is None:
            error(
                "Pillow is required for PNG operations.\n"
                "Install it with: py -m pip install Pillow"
            )

        checksum = parse_checksum(checksum)
        filename = Path(filename)

        if not filename.is_file():
            error(f"PNG file not found: {filename}")

        if replace and checksum not in self.textures:
            error(
                f"Cannot replace 0x{checksum:016x}: "
                "the checksum does not exist."
            )

        if not replace and checksum in self.textures:
            error(
                f"Checksum 0x{checksum:016x} already exists. "
                "Use --replace instead."
            )

        try:
            with Image.open(filename) as image:
                image = image.convert("RGBA")
                width, height = image.size
                pixels = image.tobytes()
        except Exception as exc:
            error(f"Could not load PNG {filename}: {exc}")

        if width <= 0 or height <= 0:
            error("PNG has invalid dimensions.")

        if len(pixels) > MAX_TEXTURE_SIZE:
            error("PNG is too large.")

        if replace:
            old = self.textures[checksum]

            texture = Texture(
                checksum=checksum,
                width=width,
                height=height,
                fmt=old.fmt & ~GL_TEXFMT_GZ,
                texture_format=old.texture_format,
                pixel_type=old.pixel_type,
                is_hires=old.is_hires,
                data=b"",
                source_location=-1,
            )

            # The old texture may use another pixel format. PNG import
            # writes RGBA8 data, so its format metadata must be updated.
            texture.fmt = GL_TEXFMT_RGBA8
            texture.texture_format = GL_RGBA
            texture.pixel_type = GL_UNSIGNED_BYTE
        else:
            texture = Texture(
                checksum=checksum,
                width=width,
                height=height,
                fmt=GL_TEXFMT_RGBA8,
                texture_format=GL_RGBA,
                pixel_type=GL_UNSIGNED_BYTE,
                is_hires=1,
                data=b"",
                source_location=-1,
            )

        # Match the original addImage() default: raw RGBA payload,
        # without the zlib compression flag.
        encode_pixels(texture, pixels, compress=False)

        self.textures[checksum] = texture

        action = "Replaced" if replace else "Inserted"
        log(
            f"{action} {filename} as checksum "
            f"0x{checksum:016x} ({width}x{height})."
        )

    # -----------------------------------------------------------------
    # HTS writer
    # -----------------------------------------------------------------

    def save_hts(self, filename):
        if not self.textures:
            error("Refusing to save an empty archive.")

        filename = Path(filename)
        temp = filename.with_name(filename.name + ".tmp")

        try:
            with temp.open("wb") as stream:
                # HTS header: int m_Config + int64 m_storagePos.
                stream.write(CONFIG.pack(self.config))
                stream.write(POINTER.pack(0))

                entries = []

                for checksum, texture in self.textures.items():
                    location = stream.tell()
                    texture.source_location = location

                    write_texture_record(
                        stream,
                        texture,
                        include_checksum=False,
                    )

                    entries.append((checksum, location))

                # Append the storage table.
                table_location = stream.tell()
                stream.write(COUNT.pack(len(entries)))

                for checksum, location in entries:
                    stream.write(
                        INDEX_ENTRY.pack(checksum, location)
                    )

                # Patch the index offset in the header.
                stream.seek(CONFIG_SIZE)
                stream.write(POINTER.pack(table_location))

            os.replace(temp, filename)

        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        log(
            f"Saved HTS: {filename} "
            f"({len(self.textures):,} texture(s))."
        )

    # -----------------------------------------------------------------
    # HTC writer
    # -----------------------------------------------------------------

    def save_htc(self, filename):
        if not self.textures:
            error("Refusing to save an empty archive.")

        filename = Path(filename)
        temp = filename.with_name(filename.name + ".tmp")

        try:
            with temp.open("wb") as raw_stream:
                with gzip.GzipFile(
                    fileobj=raw_stream,
                    mode="wb",
                    filename="",
                    mtime=0,
                    compresslevel=6,
                ) as stream:
                    # The outer gzip stream contains the configuration
                    # integer followed by sequential texture records.
                    stream.write(CONFIG.pack(self.config))

                    for texture in self.textures.values():
                        write_texture_record(
                            stream,
                            texture,
                            include_checksum=True,
                        )

            os.replace(temp, filename)

        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        log(
            f"Saved HTC: {filename} "
            f"({len(self.textures):,} texture(s))."
        )

    def save(self, filename):
        suffix = Path(filename).suffix.lower()

        if suffix == ".hts":
            self.save_hts(filename)
        elif suffix == ".htc":
            self.save_htc(filename)
        else:
            error("Output filename must end with .hts or .htc.")

    # -----------------------------------------------------------------
    # Metadata dump
    # -----------------------------------------------------------------

    def dump(self, filename="dump.txt"):
        filename = Path(filename)

        with filename.open("w", encoding="utf-8") as stream:
            stream.write(f"Archive: {self.filename}\n")
            stream.write(f"Type: {self.archive_type.upper()}\n")
            stream.write(f"Config: {self.config}\n")
            stream.write(f"Texture count: {len(self.textures)}\n\n")

            for checksum, texture in self.textures.items():
                stream.write(f"Checksum: 0x{checksum:016x}\n")
                stream.write(f"Checksum decimal: {checksum}\n")
                stream.write(
                    f"Dimensions: {texture.width} x {texture.height}\n"
                )
                stream.write(f"Format: 0x{texture.fmt:08x}\n")
                stream.write(
                    f"Texture format: 0x{texture.texture_format:04x}\n"
                )
                stream.write(
                    f"Pixel type: 0x{texture.pixel_type:04x}\n"
                )
                stream.write(f"High-resolution: {texture.is_hires}\n")
                stream.write(f"Payload size: {len(texture.data)}\n")
                stream.write(f"Zlib compressed: {texture.compressed}\n")
                stream.write(
                    f"Original location: {texture.source_location}\n\n"
                )

        log(f"Wrote dump: {filename}")


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="HTconv.py",
        description="Convert HTS, HTC, and PNG texture archives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py HTconv.py --open textures.hts --dump
  py HTconv.py --open textures.hts --export exported
  py HTconv.py --open textures.hts --save textures.htc
  py HTconv.py --open textures.htc --save textures.hts
  py HTconv.py --open textures.hts --insert new.png 0x123456 --save out.hts
  py HTconv.py --open textures.hts --replace replacement.png 0x123456 --save out.hts
        """,
    )

    parser.add_argument(
        "-open", "--open",
        dest="open_file",
        help="Input .hts or .htc archive.",
    )

    parser.add_argument(
        "-export", "--export",
        dest="export_folder",
        help="Export supported textures to a directory.",
    )

    parser.add_argument(
        "-save", "--save",
        dest="save_file",
        help="Save output as .hts or .htc.",
    )

    parser.add_argument(
        "-dump", "--dump",
        nargs="?",
        const="dump.txt",
        default=None,
        help="Dump metadata to dump.txt or a specified path.",
    )

    parser.add_argument(
        "-insert", "--insert",
        nargs=2,
        metavar=("PNG", "CHECKSUM"),
        help="Insert a PNG with the supplied checksum.",
    )

    parser.add_argument(
        "-replace", "--replace",
        nargs=2,
        metavar=("PNG", "CHECKSUM"),
        help="Replace an existing checksum with a PNG.",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.open_file:
        parser.print_help()
        return 0

    try:
        archive = HTArchive(args.open_file)

        if args.dump is not None:
            archive.dump(args.dump)

        if args.export_folder:
            archive.export_png(args.export_folder)

        if args.insert:
            png, checksum = args.insert
            archive.import_png(png, checksum, replace=False)

        if args.replace:
            png, checksum = args.replace
            archive.import_png(png, checksum, replace=True)

        if args.save_file:
            archive.save(args.save_file)

        if not any((
            args.dump is not None,
            args.export_folder,
            args.insert,
            args.replace,
            args.save_file,
        )):
            log("Archive opened successfully. No operation requested.")

        return 0

    except (
        RuntimeError,
        OSError,
        ValueError,
        struct.error,
        OverflowError,
    ) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
