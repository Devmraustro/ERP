"""Python port of sqlite's tool/mkopcodec.tcl.

Scans the generated `opcodes.h` and emits `opcodes.c`, the symbolic name table
used by EXPLAIN. Reads the file named in argv[1], writes C to stdout.
"""
import re
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8", errors="replace").read().splitlines()

label = {}
synopsis = {}
mx = 0
for line in lines:
    if line.startswith("#define OP_"):
        parts = line.split()
        name = re.sub(r"^OP_", "", parts[1])
        i = int(parts[2])
        label[i] = name
        mx = max(mx, i)
        m = re.search(r"synopsis: (.*) \*/", line)
        synopsis[i] = m.group(1).strip() if m else ""

out = []
out.append("/* Automatically generated.  Do not edit */")
out.append("/* See the tool/mkopcodec.tcl script for details. */")
out.append("#if !defined(SQLITE_OMIT_EXPLAIN) \\")
out.append(" || defined(VDBE_PROFILE) \\")
out.append(" || defined(SQLITE_DEBUG)")
out.append("#if defined(SQLITE_ENABLE_EXPLAIN_COMMENTS) || defined(SQLITE_DEBUG)")
out.append('# define OpHelp(X) "\\0" X')
out.append("#else")
out.append("# define OpHelp(X)")
out.append("#endif")
out.append("const char *sqlite3OpcodeName(int i){")
out.append(" static const char *const azName[] = {")
for i in range(0, mx + 1):
    lab = label.get(i, "")
    syn = synopsis.get(i, "")
    out.append('    /* %3d */ "%s" %-12s OpHelp("%s"),' % (i, lab, "", syn))
out.append("  };")
out.append("  return azName[i];")
out.append("}")
out.append("#endif")
sys.stdout.write("\n".join(out) + "\n")
print("opcodes.c written: %d entries" % (mx + 1), file=sys.stderr)
