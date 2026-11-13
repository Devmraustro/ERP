"""Generate /tmp/qstub: linker stubs for the system libs PySide6 dlopens.

The sandbox has no libGL/libEGL/libxkbcommon/libdbus. PySide6's Qt .so files
need those *symbol names* to load; nothing here is ever executed, so exporting
the exact UND symbols each consumer imports is sufficient and faithful.
"""

from __future__ import annotations

import glob
import subprocess
from pathlib import Path

SITE = Path("/tmp/vui/lib/python3.11/site-packages/PySide6")
OUT = Path("/tmp/qstub")
OUT.mkdir(parents=True, exist_ok=True)

BUCKETS = {
    "dbus_": "libdbus-1.so.3",
    "xkb_": "libxkbcommon.so.0",
    "egl": "libEGL.so.1",
    "glX": "libGLX.so.0",
    "gl": "libGL.so.1",
}
GL_ALSO = {"glX": "libGL.so.1"}


def und_symbols(paths) -> set[str]:
    syms: set[str] = set()
    for path in paths:
        try:
            out = subprocess.run(
                ["nm", "-D", "--undefined-only", str(path)],
                capture_output=True, text=True, check=False,
            ).stdout
        except OSError:
            continue
        for line in out.splitlines():
            parts = line.split()
            if parts:
                syms.add(parts[-1].split("@")[0])
    return syms


def main() -> None:
    consumers = sorted(glob.glob(str(SITE / "Qt" / "lib" / "*.so*")))
    consumers += sorted(glob.glob(str(SITE / "QtCore" / "*.so*")))
    consumers += sorted(glob.glob(str(SITE / "QtGui" / "*.so*")))
    consumers += sorted(glob.glob(str(SITE / "QtWidgets" / "*.so*")))
    # Qt platform plugins are dlopened at run time and are consumers too.
    consumers += sorted(glob.glob(str(SITE / "Qt" / "plugins" / "**" / "*.so*"), recursive=True))
    all_syms = und_symbols(consumers)

    mapping: dict[str, set[str]] = {lib: set() for lib in BUCKETS.values()}
    mapping["libGL.so.1"] = set()
    # Longest prefix first: "glX..." must not be captured by the "gl" bucket.
    for sym in all_syms:
        for prefix in sorted(BUCKETS, key=len, reverse=True):
            if sym.startswith(prefix):
                lib = BUCKETS[prefix]
                mapping[lib].add(sym)
                if prefix in GL_ALSO:
                    mapping[GL_ALSO[prefix]].add(sym)
                break

    for lib, syms in mapping.items():
        if not syms:
            continue
        csrc = OUT / (lib.replace(".", "_") + ".c")
        lines = ["/* auto-generated linker stub */"]
        for sym in sorted(syms):
            lines.append(f"void {sym}(void) {{}}")
        csrc.write_text("\n".join(lines) + "\n")

        base = lib
        if base.startswith("libGLX") or base.startswith("libxkbcommon"):
            node = "V_0.5.0"
        elif base.startswith("libdbus"):
            node = "LIBDBUS_1_3"
        else:
            node = "LIBSTUB_1.0"
        version = OUT / (node + ".map")
        version.write_text(node + " {\n  global: *;\n};\n")
        soname = base
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(OUT / base), str(csrc),
             f"-Wl,-soname,{soname}", f"-Wl,--version-script={version}"],
            check=True,
        )
        print(f"{base}: {len(syms)} symbols")


if __name__ == "__main__":
    main()
