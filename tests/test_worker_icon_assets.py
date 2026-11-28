"""Cross-platform Worker icons. Only stdlib; no build or Pillow dependency."""
from pathlib import Path
import json
import hashlib
import xml.etree.ElementTree as ET
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "remote_worker_gui" / "src-tauri" / "icons"
PNG = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    assert data.startswith(PNG), "missing PNG signature"
    assert data[12:16] == b"IHDR", "missing PNG header"
    width, height, depth, color, compression, filtering, interlace = struct.unpack_from(
        ">IIBBBBB", data, 16
    )
    assert (depth, color, compression, filtering, interlace) == (8, 6, 0, 0, 0)
    return width, height


def rgba(data: bytes) -> bytes:
    """Decode RGBA PNGs with the five standard lossless PNG row filters."""
    width, height = png_size(data)
    offset = 8
    idat = []
    while offset < len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        tag = data[offset + 4:offset + 8]
        start = offset + 8
        if tag == b"IDAT":
            idat.append(data[start:start + length])
        offset += 12 + length
        if tag == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    stride = width * 4
    assert len(raw) == height * (stride + 1)
    previous = bytearray(stride)
    result = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        mode = raw[start]
        current = bytearray(raw[start + 1:start + 1 + stride])
        assert mode in range(5)
        for x in range(stride):
            left = current[x - 4] if x >= 4 else 0
            up = previous[x]
            upper_left = previous[x - 4] if x >= 4 else 0
            if mode == 1:
                current[x] = (current[x] + left) & 255
            elif mode == 2:
                current[x] = (current[x] + up) & 255
            elif mode == 3:
                current[x] = (current[x] + ((left + up) // 2)) & 255
            elif mode == 4:
                p = left + up - upper_left
                dl, du, dul = abs(p - left), abs(p - up), abs(p - upper_left)
                predictor = left if dl <= du and dl <= dul else up if du <= dul else upper_left
                current[x] = (current[x] + predictor) & 255
        result.extend(current)
        previous = current
    return bytes(result)


# SHA-256 of decoded 64x64 RGBA pixels from main 2e098d3.
ORIGINAL_RGBA_SHA256 = "27080740025e3cf527633f7a82fab579fe4813243aea61cf7f79a7d7e7bce521"


def original_pixels():
    master = (ICONS / "icon.png").read_bytes()
    assert png_size(master) == (512, 512)
    full = rgba(master)
    source = b"".join(full[(y * 8 * 512 + x * 8) * 4:(y * 8 * 512 + x * 8) * 4 + 4]
                      for y in range(64) for x in range(64))
    assert hashlib.sha256(source).hexdigest() == ORIGINAL_RGBA_SHA256
    return source


def assert_matches_original(data, size, source):
    assert png_size(data) == (size, size)
    expected = bytearray()
    for y in range(size):
        for x in range(size):
            pos = ((2*y+1)*64//(2*size)*64 + (2*x+1)*64//(2*size))*4
            expected.extend(source[pos:pos+4])
    assert rgba(data) == expected


def test_linux_png_sizes_and_same_exact_source_graphic():
    source = original_pixels()
    for name, size in (("icon.png", 512), ("32x32.png", 32),
                       ("128x128.png", 128), ("128x128@2x.png", 256)):
        assert_matches_original((ICONS / name).read_bytes(), size, source)


def test_windows_ico_has_each_required_layer():
    data = (ICONS / "icon.ico").read_bytes()
    assert struct.unpack_from("<HHH", data) == (0, 1, 6)
    source = original_pixels()
    sizes = []
    spans = []
    for index in range(6):
        w, h, colors, reserved, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16)
        size = w or 256
        assert size == (h or 256)
        assert (colors, reserved, planes, bpp) == (0, 0, 1, 32)
        assert offset >= 102 and length > 0 and offset + length <= len(data)
        assert_matches_original(data[offset:offset+length], size, source)
        sizes.append(size)
        spans.append((offset, offset + length))
    assert sizes == [32, 16, 24, 48, 64, 256]
    spans.sort()
    assert spans[0][0] == 102 and spans[-1][1] == len(data)
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))


def test_tauri_config_lists_native_icons_without_enabling_bundle_release():
    config = json.loads((ROOT / "remote_worker_gui/src-tauri/tauri.conf.json").read_text())
    assert config["bundle"]["active"] is False
    assert config["bundle"]["icon"] == [
        "icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png",
        "icons/icon.png", "icons/icon.ico",
    ]


def test_android_icons_generated_after_android_init_and_before_build():
    workflow = (ROOT / ".github/workflows/android-worker-apk.yml").read_text()
    init = workflow.index("npx tauri android init --ci")
    generate = workflow.index("npx tauri icon src-tauri/icons/android-pawn.svg --output src-tauri/icons/android-generated")
    compile_index = workflow.index("tauri android build --apk")
    assert init < generate < compile_index
    assert "npx tauri icon src-tauri/icons/icon.png" not in workflow
    assert "source_dir=src-tauri/icons/android-generated/android" in workflow
    assert "resource_dir=src-tauri/gen/android/app/src/main/res" in workflow
    assert 'install -D -m 644 "$file" "$resource_dir/$relative"' in workflow
    for resource in ("mipmap-mdpi/ic_launcher.png",
                     "mipmap-xxxhdpi/ic_launcher_foreground.png"):
        assert f'cmp "$source_dir/{resource}" "$resource_dir/{resource}"' in workflow
    assert (ICONS / "android-pawn.svg").is_file()
    assert "mipmap-mdpi/ic_launcher.png" in workflow
    assert "mipmap-xxxhdpi/ic_launcher_foreground.png" in workflow
    # No manual build without actual Worker code/resource change.
    assert not any(line.strip() == "workflow_dispatch:" for line in workflow.splitlines())


def test_platform_filters_rebuild_only_affected_icons():
    workflow = (ROOT / ".github/workflows/worker-multiplatform.yml").read_text()
    assert "'remote_worker_gui/src-tauri/icons/icon.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/icon.ico'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/32x32.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/128x128.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/128x128@2x.png'" in workflow
    assert not any(line.strip() == "workflow_dispatch:" for line in workflow.splitlines())


def test_android_launcher_uses_the_web_panel_pawn_without_external_assets():
    """Exact U+265F symbol from index.html, rendered as standalone vector for Android."""
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "<h1>♟ Local AI Chess Arena</h1>" in index
    svg_path = ICONS / "android-pawn.svg"
    svg = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox") == "0 0 512 512"
    assert (root.get("width"), root.get("height")) == ("512", "512")
    ns = "{http://www.w3.org/2000/svg}"
    assert [child.tag for child in root] == [ns+"title", ns+"desc", ns+"rect", ns+"path"]
    rect = root.find(ns+"rect")
    assert rect is not None
    assert tuple(rect.get(k) for k in ("x", "y", "width", "height", "fill")) == (
        "0", "0", "512", "512", "#10151d")
    assert all(not any(k.endswith("href") for k in node.attrib) for node in root.iter())
    assert "U+265F" in svg
    assert "#10151d" in svg and "#e8edf5" in svg
    shapes = root.findall("{http://www.w3.org/2000/svg}path")
    assert len(shapes) == 1 and len(shapes[0].get("d", "")) > 200
    assert shapes[0].get("fill") == "#e8edf5"
    assert not any(token in svg for token in ("https://", "@font-face", "<image", "xlink:href", "<text"))
    assert svg.count("http://") == 1  # The SVG namespace URI only.
