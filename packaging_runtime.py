"""Keep one compatible Microsoft C++ runtime throughout a Windows bundle.

WinRT's wheel includes an old MSVCP140.dll. Windows can load that copy before
CTranslate2, whose newer mutex ABI then crashes with an access violation. Merely
adding a newer DLL at the bundle root does not replace the adjacent WinRT copy.
"""
import ctypes
import os
from pathlib import Path, PureWindowsPath
import struct


MIN_RUNTIME_VERSION = (14, 40, 0, 0)
RUNTIME_NAMES = frozenset({
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll",
    "concrt140.dll",
})


def inspect_runtime(path):
    """Return PE machine and numeric file version without loading the DLL."""
    path = Path(path)
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"Not a Windows DLL: {path}")
        stream.seek(60)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"Invalid PE header: {path}")
        machine = struct.unpack("<H", stream.read(2))[0]

    version_api = ctypes.WinDLL("version", use_last_error=True)
    version_api.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    version_api.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version_api.GetFileVersionInfoW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    version_api.GetFileVersionInfoW.restype = ctypes.c_int
    version_api.VerQueryValueW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32)]
    version_api.VerQueryValueW.restype = ctypes.c_int
    size = version_api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ValueError(f"Missing DLL version: {path}")
    buffer = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not version_api.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    if not version_api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer),
                                      ctypes.byref(length)) or length.value < 16:
        raise ValueError(f"Invalid DLL version: {path}")
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    if fixed[0] != 0xFEEF04BD:
        raise ValueError(f"Invalid DLL version signature: {path}")
    return machine, (fixed[2] >> 16, fixed[2] & 0xffff,
                     fixed[3] >> 16, fixed[3] & 0xffff)


def normalize_runtime_entries(entries, runtime_dir=None):
    """Replace ALL shared-runtime copies, preserving their bundle destinations.

    LYRIO_MSVC_RUNTIME_DIR can pin a Microsoft x64 redistributable directory on
    a release builder. Otherwise use its installed System32 redistributable.
    Refuse an outdated, mixed-version, or wrong-architecture runtime set.
    Renamed private runtimes (e.g. rapidfuzz's hash-suffixed DLL) are untouched.
    """
    if runtime_dir is None:
        runtime_dir = os.environ.get("LYRIO_MSVC_RUNTIME_DIR") or (
            Path(os.environ["SystemRoot"]) / "System32")
    runtime_dir = Path(runtime_dir)
    replacements, versions = {}, set()
    metadata = {}

    def inspect(path):
        key = str(path)
        if key not in metadata:
            metadata[key] = inspect_runtime(path)
        return metadata[key]

    for destination, source, kind in entries:
        name = PureWindowsPath(destination).name.lower()
        if name not in RUNTIME_NAMES:
            continue
        replacement = runtime_dir / name
        try:
            machine, version = inspect(replacement)
        except (OSError, ValueError) as error:
            raise RuntimeError(f"Cannot package Microsoft runtime {replacement}: {error}") from error
        if machine != 0x8664:
            raise RuntimeError(f"Microsoft runtime must be x64: {replacement}")
        if version < MIN_RUNTIME_VERSION:
            raise RuntimeError(f"Microsoft runtime must be 14.40 or newer: {replacement}")
        _old_machine, old_version = inspect(source)
        if version < old_version:
            raise RuntimeError(f"Refusing to downgrade {name}: {version} < {old_version}")
        replacements[name] = str(replacement)
        versions.add(version)
    if not replacements:
        raise RuntimeError("No Microsoft runtime found in the Windows bundle")
    if len(versions) != 1:
        raise RuntimeError(f"Microsoft runtime DLLs must come from one release: {versions}")
    print("Bundling Microsoft C++ runtime " + ".".join(map(str, next(iter(versions))))
          + f" from {runtime_dir}")
    return [(destination, replacements.get(PureWindowsPath(destination).name.lower(), source), kind)
            for destination, source, kind in entries]
