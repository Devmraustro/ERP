#!/bin/bash
# Build a supported CPython (>=3.11.17 or 3.12.10) for the ERP test suite,
# plus the extension modules the image lacks (sqlite3, ssl, hashlib, ctypes).
#
# The sandbox has no package manager and no dev headers, so every dependency
# is built from source or from an official wheel.  Everything goes under /tmp
# and nothing is installed system-wide.
#
# Usage:  bash build_env.sh [python_version]
#         python_version default: 3.12.10
#
# Result: $PY_PREFIX/bin/python3 (default /tmp/py312)
set -euo pipefail

PYVER="${1:-3.12.10}"
PY_PREFIX="${PY_PREFIX:-/tmp/py312}"
ZLIB_PREFIX=/tmp/zlibbuild
SQLITE_VER=3.45.3
OPENSSL_VER=3.0.20
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=/tmp/envbuild
mkdir -p "$WORK" && cd "$WORK"

log() { echo "==> $*"; }

# ---------------------------------------------------------------- zlib
if [ ! -f "$ZLIB_PREFIX/lib/libz.so" ]; then
  log "zlib 1.3.1"
  curl -sfL https://codeload.github.com/madler/zlib/tar.gz/refs/tags/v1.3.1 -o zl.tgz
  tar xzf zl.tgz
  ( cd zlib-1.3.1 && ./configure --prefix=$ZLIB_PREFIX >/dev/null && make -j2 >/dev/null && make install >/dev/null )
fi

# ---------------------------------------------------------------- cpython
if [ ! -x "$PY_PREFIX/bin/python3" ]; then
  log "CPython $PYVER"
  curl -sfL "https://codeload.github.com/python/cpython/tar.gz/refs/tags/v$PYVER" -o cp.tgz
  tar xzf cp.tgz
  cd "cpython-$PYVER"
  CPPFLAGS="-I$ZLIB_PREFIX/include" LDFLAGS="-L$ZLIB_PREFIX/lib" \
    ./configure --prefix="$PY_PREFIX" --with-ensurepip=install --disable-test-modules >cfg.log 2>&1
  make -j2 >make.log 2>&1
  make install >install.log 2>&1
  cd ..
fi
export LD_LIBRARY_PATH="$ZLIB_PREFIX/lib"
"$PY_PREFIX/bin/python3" -c "import tarfile;assert hasattr(tarfile,'data_filter')" \
  || { echo "FATAL: interpreter lacks tarfile.data_filter (restore guard will refuse)"; exit 1; }

# ---------------------------------------------------------------- sqlite3
if ! "$PY_PREFIX/bin/python3" -c "import sqlite3" 2>/dev/null; then
  log "sqlite $SQLITE_VER amalgamation (tclsh-free)"
  [ -d "sqlite-version-$SQLITE_VER" ] || { curl -sfL "https://codeload.github.com/sqlite/sqlite/tar.gz/refs/tags/version-$SQLITE_VER" -o sq.tgz; tar xzf sq.tgz; }
  cd "sqlite-version-$SQLITE_VER"
  ./configure >sqlcfg.log 2>&1 || true
  # lemon generates the parser; it is a plain C program, no tclsh needed.
  gcc -o lemon tool/lemon.c && cp tool/lempar.c . && cp src/parse.y . && ./lemon -S parse.y >/dev/null
  # The generated opcode steps need tclsh; replace them with the Python ports.
  cat parse.h src/vdbe.c | python3 "$HERE/mkopcodeh.py" > opcodes.h
  python3 "$HERE/mkopcodec.py" opcodes.h > opcodes.c 2>/dev/null
  python3 "$HERE/mksqlite3h.py" "$PWD" sqlite3.h
  echo "/* stub: shell.c not needed for _sqlite3 */" > shell.c
  HERE="$HERE" python3 - <<'PY'
import os, re, pathlib
HERE = os.environ["HERE"]
p = pathlib.Path("Makefile"); s = p.read_text()
s = s.replace("has_tclsh84:\n\tsh $(TOP)/tool/cktclsh.sh 8.4 $(TCLSH_CMD)\n\ttouch has_tclsh84", "has_tclsh84:\n\ttouch has_tclsh84")
s = s.replace("has_tclsh85:\n\tsh $(TOP)/tool/cktclsh.sh 8.5 $(TCLSH_CMD)\n\ttouch has_tclsh85", "has_tclsh85:\n\ttouch has_tclsh85")
s = s.replace("\t$(TCLSH_CMD) $(TOP)/tool/vdbe-compress.tcl $(OPTS) <tsrc/vdbe.c >vdbe.new", "\tcat tsrc/vdbe.c >vdbe.new")
s = s.replace("fts5.c: $(FTS5_SRC) has_tclsh84\n\t$(TCLSH_CMD) $(TOP)/ext/fts5/tool/mkfts5c.tcl\n\tcp $(TOP)/ext/fts5/fts5.h .",
              "fts5.c: $(FTS5_SRC) has_tclsh84\n\tcp $(TOP)/ext/fts5/fts5.h .\n\ttouch fts5.c")
lines = s.splitlines()
for i, l in enumerate(lines):
    if "$(TCLSH_CMD) $(TOP)/tool/mksqlite3c.tcl" in l:
        lines[i] = "\tpython3 %s/mksqlite3c.py" % HERE
s = "\n".join(lines) + "\n"
p.write_text(s)
PY
  make sqlite3.c >amal.log 2>&1
  # fix the double backslash the ported generator can leave on continuation lines
  python3 - <<'PY'
import pathlib
p = pathlib.Path("sqlite3.c"); s = p.read_text()
s = s.replace("\\\\\n", "\\\n")
p.write_text(s)
PY
  gcc -c -O2 -fPIC -I. sqlite3.c -o /tmp/sqlite3.o
  cd ..
  # _sqlite3 needs the CPython source tree
  mkdir -p /tmp/sqobjs && rm -f /tmp/sqobjs/*.o
  for f in cpython-$PYVER/Modules/_sqlite/*.c; do
    gcc -c -fPIC -O2 -I"$WORK/sqlite-version-$SQLITE_VER" -I"$WORK/cpython-$PYVER" \
        -I"$WORK/cpython-$PYVER/Include" -I"$WORK/cpython-$PYVER/Include/internal" \
        -I"$PY_PREFIX/include/python${PYVER%.*}" -o "/tmp/sqobjs/$(basename "${f%.c}").o" "$f"
  done
  DYN=$("$PY_PREFIX/bin/python3" -c "import sysconfig;print(sysconfig.get_config_var('DESTSHARED') or '')")
  [ -n "$DYN" ] || { echo "FATAL: no lib-dynload path"; exit 1; }
  gcc -shared -o "$DYN/_sqlite3.cpython-${PYVER%.*}-x86_64-linux-gnu.so" /tmp/sqobjs/*.o /tmp/sqlite3.o -lpthread -ldl -lm
  "$PY_PREFIX/bin/python3" -c "import sqlite3;print('sqlite3', sqlite3.sqlite_version)"
fi

# ---------------------------------------------------------------- openssl headers
if [ ! -f "$WORK/openssl-openssl-$OPENSSL_VER/include/openssl/opensslv.h" ]; then
  log "OpenSSL $OPENSSL_VER headers"
  curl -sfL "https://codeload.github.com/openssl/openssl/tar.gz/refs/tags/openssl-$OPENSSL_VER" -o os.tgz
  tar xzf os.tgz
  ( cd "openssl-openssl-$OPENSSL_VER" && ./Configure linux-x86_64 no-tests >/dev/null 2>&1 && make build_generated >/dev/null 2>&1 ) || true
  # configuration.h/opensslv.h are Perl-generated; substitute the tokens directly.
  python3 - "$WORK/openssl-openssl-$OPENSSL_VER/include/openssl/opensslv.h" <<'PY'
import re, pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = s.replace('"OpenSSL {- "$config{full_version} $config{release_date}" -}"', '"OpenSSL 3.0.20 7 Apr 2026"')
s = s.replace("{- $config{major} -}", "3").replace("{- $config{minor} -}", "0").replace("{- $config{patch} -}", "20")
s = re.sub(r"\{-.*?-\}", "", s)
p.write_text(s)
PY
fi
SSLINC="$WORK/openssl-openssl-$OPENSSL_VER/include"
SYSLIB=/usr/lib/x86_64-linux-gnu

# ---------------------------------------------------------------- _ssl / _hashlib
build_mod() {  # $1=module  $2=source  $3=extra libs
  local mod="$1" src="$2"; shift 2
  gcc -c -fPIC -O2 -I"$SSLINC" -I"$WORK/cpython-$PYVER" -I"$WORK/cpython-$PYVER/Include" \
      -I"$WORK/cpython-$PYVER/Include/internal" -I"$PY_PREFIX/include/python${PYVER%.*}" \
      -o "/tmp/${mod}.o" "$src"
  local dyn; dyn=$("$PY_PREFIX/bin/python3" -c "import sysconfig;print(sysconfig.get_config_var('DESTSHARED'))")
  gcc -shared -o "$dyn/${mod}.cpython-${PYVER%.*}-x86_64-linux-gnu.so" "/tmp/${mod}.o" "$@"
}
if ! "$PY_PREFIX/bin/python3" -c "import ssl" 2>/dev/null; then
  log "_ssl"
  build_mod _ssl "$WORK/cpython-$PYVER/Modules/_ssl.c" "$SYSLIB/libssl.so.3" "$SYSLIB/libcrypto.so.3"
fi
if ! "$PY_PREFIX/bin/python3" -c "import hashlib;hashlib.pbkdf2_hmac" 2>/dev/null; then
  log "_hashlib (provides hashlib.pbkdf2_hmac)"
  build_mod _hashlib "$WORK/cpython-$PYVER/Modules/_hashopenssl.c" "$SYSLIB/libssl.so.3" "$SYSLIB/libcrypto.so.3"
fi

# ---------------------------------------------------------------- _ctypes
if ! "$PY_PREFIX/bin/python3" -c "import _ctypes" 2>/dev/null; then
  log "_ctypes (minimal ffi.h; ffi_abi UNIX64=2)"
  mkdir -p /tmp/ffihdr
  cp "$HERE/ffi.h" /tmp/ffihdr/ffi.h
  mkdir -p /tmp/ctobj && rm -f /tmp/ctobj/*.o
  for f in _ctypes callbacks callproc stgdict cfield; do
    gcc -c -fPIC -O2 -I/tmp/ffihdr -I"$WORK/cpython-$PYVER" -I"$WORK/cpython-$PYVER/Include" \
        -I"$WORK/cpython-$PYVER/Include/internal" -I"$PY_PREFIX/include/python${PYVER%.*}" \
        -o "/tmp/ctobj/$f.o" "$WORK/cpython-$PYVER/Modules/_ctypes/$f.c"
  done
  cat > /tmp/ctobj/ffi_compat.c <<'EOF'
/* libffi 4-style aliases for legacy ffi_type_* names referenced by _ctypes;
   the image ships libffi.so.8 which only exports the fixed-width types. */
#include "ffi.h"
ffi_type ffi_type_schar  = { 1, 1, FFI_TYPE_SINT8,  0 };
ffi_type ffi_type_uchar  = { 1, 1, FFI_TYPE_UINT8,  0 };
ffi_type ffi_type_sshort = { 2, 2, FFI_TYPE_SINT16, 0 };
ffi_type ffi_type_ushort = { 2, 2, FFI_TYPE_UINT16, 0 };
ffi_type ffi_type_sint   = { 4, 4, FFI_TYPE_SINT32, 0 };
ffi_type ffi_type_uint   = { 4, 4, FFI_TYPE_UINT32, 0 };
ffi_type ffi_type_slong  = { 8, 8, FFI_TYPE_SINT64, 0 };
ffi_type ffi_type_ulong  = { 8, 8, FFI_TYPE_UINT64, 0 };
EOF
  gcc -c -fPIC -O2 -I/tmp/ffihdr -o /tmp/ctobj/ffi_compat.o /tmp/ctobj/ffi_compat.c
  DYN=$("$PY_PREFIX/bin/python3" -c "import sysconfig;print(sysconfig.get_config_var('DESTSHARED'))")
  gcc -shared -o "$DYN/_ctypes.cpython-${PYVER%.*}-x86_64-linux-gnu.so" /tmp/ctobj/*.o "$SYSLIB/libffi.so.8"
fi

log "final check"
"$PY_PREFIX/bin/python3" - <<'PY'
import sys, tarfile
print("python     :", sys.version.split()[0])
print("data_filter:", hasattr(tarfile, "data_filter"))
for m in ("sqlite3", "ssl", "hashlib", "_ctypes"):
    try:
        mod = __import__(m)
        extra = ""
        if m == "sqlite3":
            extra = mod.sqlite_version
        if m == "ssl":
            extra = mod.OPENSSL_VERSION
        if m == "hashlib":
            extra = "pbkdf2_hmac ok"
        print(f"{m:10s} : OK {extra}")
    except Exception as e:
        print(f"{m:10s} : FAIL {e}")
PY
echo "DONE: export LD_LIBRARY_PATH=$ZLIB_PREFIX/lib; $PY_PREFIX/bin/python3"
