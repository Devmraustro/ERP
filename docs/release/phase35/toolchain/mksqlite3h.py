"""Python port of sqlite's tool/mksqlite3h.tcl.

Builds `sqlite3.h` from `src/sqlite.h.in` (+ rtree/session/fts5 headers):
prefixes SQLITE_API on declarations and substitutes version/source-id tokens.
Usage: python3 mksqlite3h.py <TOP> <OUTFILE>
"""
import os
import re
import sys

TOP = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else None

z_version = open(os.path.join(TOP, "VERSION")).read().strip()
a, b, c = z_version.split(".")
n_version = "%d%03d%03d" % (int(a), int(b), int(c))
z_source_id = "2024-04-15 20:00:00 python-port"

filelist = [
    os.path.join(TOP, "src/sqlite.h.in"),
    os.path.join(TOP, "ext/rtree/sqlite3rtree.h"),
    os.path.join(TOP, "ext/session/sqlite3session.h"),
    os.path.join(TOP, "ext/fts5/fts5.h"),
]

varpattern = re.compile(r"^[a-zA-Z][a-zA-Z_0-9 *]+sqlite3_[_a-zA-Z0-9]+(\[|;| =)")
declpats = [
    re.compile(r"^ *([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite3_[_a-zA-Z0-9]+)(\(.*)$"),
    re.compile(r"^ *([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite3session_[_a-zA-Z0-9]+)(\(.*)$"),
    re.compile(r"^ *([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite3changeset_[_a-zA-Z0-9]+)(\(.*)$"),
    re.compile(r"^ *([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite3changegroup_[_a-zA-Z0-9]+)(\(.*)$"),
    re.compile(r"^ *([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite3rebaser_[_a-zA-Z0-9]+)(\(.*)$"),
]

out = []
for path in filelist:
    if not os.path.exists(path):
        continue
    base = os.path.basename(path)
    if "sqlite.h.in" not in path:
        out.append("/******** Begin file %s *********/" % base)
    for raw in open(path, encoding="utf-8", errors="replace").read().splitlines():
        line = raw.rstrip()
        if re.search(r'#include.*[<"]sqlite3\.h[>"]', line):
            continue
        line = line.replace("--VERS--", z_version)
        line = line.replace("--VERSION-NUMBER--", n_version)
        line = line.replace("--SOURCE-ID--", z_source_id)
        if varpattern.search(line) and not re.match(r"^ *typedef", line):
            line = "SQLITE_API " + line
        else:
            for pat in declpats:
                m = pat.match(line)
                if m:
                    rettype, funcname, rest = m.group(1), m.group(2), m.group(3)
                    newline = "SQLITE_API " + rettype.strip()
                    if not rettype.endswith("*"):
                        newline += " "
                    line = newline + funcname + rest
                    break
        out.append(line)
    if "sqlite.h.in" not in path:
        out.append("/******** End of %s *********/" % base)

text = "\n".join(out) + "\n"
if OUT:
    open(OUT, "w", encoding="utf-8").write(text)
else:
    sys.stdout.write(text)
print("sqlite3.h written: %d lines, version %s" % (len(out), z_version), file=sys.stderr)
