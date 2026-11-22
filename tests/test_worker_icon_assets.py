"""Cross-platform Worker icons. Only stdlib; no build or Pillow dependency."""
from pathlib import Path
import json
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


def test_linux_png_sizes_and_same_exact_source_graphic():
    master = (ICONS / "icon.png").read_bytes()
    assert png_size(master) == (512, 512)
    full = rgba(master)
    colors = {tuple(full[i:i + 4]) for i in range(0, len(full), 4)}
    assert colors == {(18, 30, 49, 255), (129, 140, 248, 255)}
    for name, size in (
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("128x128@2x.png", 256),
    ):
        data = (ICONS / name).read_bytes()
        assert png_size(data) == (size, size)
        pixels = rgba(data)
        step = 512 // size
        for y in range(size):
            for x in range(size):
                pos = (y * size + x) * 4
                master_pos = ((y * step) * 512 + (x * step)) * 4
                assert pixels[pos:pos + 4] == full[master_pos:master_pos + 4]


def test_windows_ico_has_each_required_layer():
    data = (ICONS / "icon.ico").read_bytes()
    assert struct.unpack_from("<HHH", data, 0) == (0, 1, 6)
    sizes = []
    for index in range(6):
        width, height, colors, reserved, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        size = width or 256
        assert size == (height or 256)
        assert (colors, reserved, planes, bpp) == (0, 0, 1, 32)
        payload = data[offset:offset + length]
        assert len(payload) == length
        assert png_size(payload) == (size, size)
        sizes.append(size)
    assert sizes == [32, 16, 24, 48, 64, 256]


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
    generate = workflow.index("npx tauri icon src-tauri/icons/icon.png")
    compile_index = workflow.index("npx tauri android build")
    assert init < generate < compile_index
    assert "mipmap-mdpi/ic_launcher.png" in workflow
    assert "mipmap-xxxhdpi/ic_launcher_foreground.png" in workflow
    # No manual build without actual Worker code/resource change.
    assert "workflow_dispatch:" not in workflow


def test_platform_filters_rebuild_only_affected_icons():
    workflow = (ROOT / ".github/workflows/worker-multiplatform.yml").read_text()
    assert "'remote_worker_gui/src-tauri/icons/icon.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/icon.ico'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/32x32.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/128x128.png'" in workflow
    assert "'remote_worker_gui/src-tauri/icons/128x128@2x.png'" in workflow
    assert "workflow_dispatch:" not in workflow
