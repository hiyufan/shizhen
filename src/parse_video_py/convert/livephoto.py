"""Pairing metadata for Live Photos (iOS) and Motion Photos (Android).

iOS pairs a JPEG and a MOV through one UUID: the MOV carries it in the
`com.apple.quicktime.content.identifier` mdta key (ffmpeg writes that for us
with `-movflags use_metadata_tags`), the JPEG carries it in the Apple
MakerNote, tag 0x0011. Android's Motion Photo is a single JPEG with the MP4
appended and an XMP block that says where the video starts.
"""
from __future__ import annotations

import struct
import uuid
from pathlib import Path

import piexif

APPLE_CONTENT_IDENTIFIER = 0x0011
APPLE_MAKERNOTE_HEADER = b"Apple iOS\x00" + b"\x00\x01" + b"MM"  # 14 bytes, big endian


def new_identifier() -> str:
    return str(uuid.uuid4()).upper()


def build_apple_makernote(identifier: str) -> bytes:
    """Minimal Apple MakerNote: one IFD holding ContentIdentifier as ASCII.

    Offsets inside an Apple MakerNote are relative to its own first byte.
    """
    value = identifier.encode("ascii") + b"\x00"
    entries = [(APPLE_CONTENT_IDENTIFIER, 2, len(value))]  # (tag, ASCII, count)
    ifd_offset = len(APPLE_MAKERNOTE_HEADER)
    data_offset = ifd_offset + 2 + 12 * len(entries) + 4
    ifd = struct.pack(">H", len(entries))
    ifd += struct.pack(">HHII", APPLE_CONTENT_IDENTIFIER, 2, len(value), data_offset)
    ifd += struct.pack(">I", 0)
    return APPLE_MAKERNOTE_HEADER + ifd + value


def write_jpeg_identifier(jpeg_path: str | Path, identifier: str, *, date: str | None = None) -> None:
    jpeg_path = str(jpeg_path)
    try:
        exif = piexif.load(jpeg_path)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    exif.setdefault("0th", {})
    exif.setdefault("Exif", {})
    exif["0th"][piexif.ImageIFD.Make] = b"Apple"
    exif["0th"][piexif.ImageIFD.Software] = b"ShiZhen"
    exif["Exif"][piexif.ExifIFD.MakerNote] = build_apple_makernote(identifier)
    if date:
        exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = date.encode()
        exif["0th"][piexif.ImageIFD.DateTime] = date.encode()
    piexif.insert(piexif.dump(exif), jpeg_path)


def read_jpeg_identifier(jpeg_path: str | Path) -> str | None:
    """Used by tests / self-check: pull the identifier back out."""
    try:
        note = piexif.load(str(jpeg_path))["Exif"].get(piexif.ExifIFD.MakerNote)
    except Exception:
        return None
    if not note or not note.startswith(b"Apple iOS\x00"):
        return None
    count = struct.unpack(">H", note[14:16])[0]
    for i in range(count):
        tag, typ, n, off = struct.unpack(">HHII", note[16 + 12 * i:28 + 12 * i])
        if tag == APPLE_CONTENT_IDENTIFIER and typ == 2:
            return note[off:off + n].rstrip(b"\x00").decode("ascii", "replace")
    return None


# ---------------------------------------------------------------- Motion Photo

_XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"


def _motion_photo_xmp(video_len: int, presentation_us: int) -> bytes:
    xml = f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="ShiZhen">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
    xmlns:Container="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
    GCamera:MotionPhoto="1"
    GCamera:MotionPhotoVersion="1"
    GCamera:MotionPhotoPresentationTimestampUs="{presentation_us}"
    GCamera:MicroVideo="1"
    GCamera:MicroVideoVersion="1"
    GCamera:MicroVideoOffset="{video_len}"
    GCamera:MicroVideoPresentationTimestampUs="{presentation_us}">
   <Container:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Length="0" Item:Padding="0"/>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{video_len}" Item:Padding="0"/>
     </rdf:li>
    </rdf:Seq>
   </Container:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""
    return xml.encode("utf-8")


def _split_jpeg_header(data: bytes) -> int:
    """Byte offset right after the leading APP0/APP1 segments (where XMP goes)."""
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    pos = 2
    while pos + 4 <= len(data) and data[pos] == 0xFF and data[pos + 1] in (0xE0, 0xE1):
        seg_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        pos += 2 + seg_len
    return pos


def write_motion_photo(jpeg_path: str | Path, mp4_path: str | Path, out_path: str | Path,
                       presentation_us: int = 0) -> None:
    jpeg = Path(jpeg_path).read_bytes()
    video = Path(mp4_path).read_bytes()
    xmp = _XMP_NS + _motion_photo_xmp(len(video), presentation_us)
    segment = b"\xff\xe1" + struct.pack(">H", len(xmp) + 2) + xmp
    cut = _split_jpeg_header(jpeg)
    Path(out_path).write_bytes(jpeg[:cut] + segment + jpeg[cut:] + video)


def mov_has_identifier(mov_path: str | Path, identifier: str) -> bool:
    data = Path(mov_path).read_bytes()
    return b"com.apple.quicktime.content.identifier" in data and identifier.encode() in data
