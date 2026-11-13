"""Python port of sqlite's tool/mksqlite3c.tcl.

Assembles the single-file amalgamation `sqlite3.c` from the prepared `tsrc/`
directory: inlines the listed headers once, rewrites linkage
(SQLITE_PRIVATE/SQLITE_API), comments out repeated includes and
`#ifdef __cplusplus` guards, drops `#line` directives (--linemacros=0), and
emits the source-id stub at the end.

Run from the sqlite source root after `make target_source`.
"""
import os
import re
import sys

SRCDIR = "tsrc"
OUT = "sqlite3.c"

version = "?????"
for line in open(os.path.join(SRCDIR, "sqlite3.h"), encoding="utf-8", errors="replace"):
    m = re.search(r'#define\s+SQLITE_VERSION\s+"(.*)"', line)
    if m:
        version = m.group(1)

out = []
out.append("""/******************************************************************************
** This file is an amalgamation of many separate C source files from SQLite
** version %s.  By combining all the individual C code files into this
** single large file, the entire code can be compiled as a single translation
** unit.  This allows many compilers to do optimizations that would not be
** possible if the files were compiled separately.  Performance improvements
** of 5%% or more are commonly seen when SQLite is compiled as a single
** translation unit.
**
** This file is all you need to compile SQLite.  To use SQLite in other
** programs, you need this file and the "sqlite3.h" header file that defines
** the programming interface to the SQLite library.  (If you do not have
** the "sqlite3.h" header file at hand, you will find a copy embedded within
** the text of this file.  Search for "Begin file sqlite3.h" to find the start
** of the embedded sqlite3.h header file.) Additional code files may be needed
** if you want a wrapper to interface SQLite with your choice of programming
** language. The code for the "sqlite3" command-line shell is also in a
** separate file. This file contains only code for the core SQLite library.
**
** The origin of the sources used to build this amalgamation
** is unknown.
*/""" % version)
out.append("""#define SQLITE_CORE 1
#define SQLITE_AMALGAMATION 1""")
out.append("""#ifndef SQLITE_PRIVATE
# define SQLITE_PRIVATE static
#endif""")

if "ifndef SQLITE_ENABLE_UPDATE_DELETE_LIMIT" in open(os.path.join(SRCDIR, "parse.c"), encoding="utf-8", errors="replace").read():
    out.append("#define SQLITE_UDL_CAPABLE_PARSER 1")

HDRS = """btree.h btreeInt.h fts3.h fts3Int.h fts3_hash.h fts3_tokenizer.h geopoly.c
hash.h hwtime.h keywordhash.h msvc.h mutex.h opcodes.h os_common.h os_setup.h
os_win.h os.h pager.h parse.h pcache.h pragma.h rtree.h sqlite3session.h sqlite3.h
sqlite3ext.h sqlite3rbu.h sqliteicu.h sqliteInt.h sqliteLimit.h vdbe.h vdbeInt.h
vxworks.h wal.h whereInt.h sqlite3recover.h""".split()

available_hdr = {h: 1 for h in HDRS}
available_hdr["sqliteInt.h"] = 0
available_hdr["os_common.h"] = 0
available_hdr["sqlite3session.h"] = 0

varonly_hdr = {"sqlite3.h": 1}
seen_hdr = {}


def section_comment(text):
    out.append("/************** %s %s/" % (text, "*" * (60 - len(text))))


INCLUDE_RE = re.compile(r'^\s*#\s*include\s+["<]([^">]+)[">]')
VAR_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z_0-9 *]+(sqlite3[_a-zA-Z0-9]+)(\[|;| =)")


def copy_file(filename):
    tail = os.path.basename(filename)
    section_comment("Begin file %s" % tail)
    decl_base = r"([a-zA-Z][a-zA-Z_0-9 ]+ \**)(sqlite[_a-zA-Z0-9]+)(\(.*)"
    if filename.endswith(".h"):
        decl_base = " *" + decl_base
    decl_pattern = re.compile("^" + decl_base + "$")

    for raw in open(filename, encoding="utf-8", errors="replace").read().splitlines():
        line = raw.rstrip()
        m = INCLUDE_RE.match(line)
        if m:
            hdr = m.group(1)
            if hdr in available_hdr:
                if available_hdr[hdr]:
                    available_hdr[hdr] = 0
                    section_comment("Include %s in the middle of %s" % (hdr, tail))
                    copy_file(os.path.join(SRCDIR, hdr))
                    section_comment("Continuing where we left off in %s" % tail)
                else:
                    out.append("/* %s */" % line.replace("/*", "**").replace("*/", "**"))
            elif hdr not in seen_hdr:
                if not re.search(r"/\*\s+amalgamator:\s+dontcache\s+\*/", line):
                    seen_hdr[hdr] = 1
                out.append(line)
            elif re.search(r"/\*\s+amalgamator:\s+keep\s+\*/", line):
                out.append(line)
            else:
                out.append("/* %s */" % line.replace("/*", "**").replace("*/", "**"))
        elif line.startswith("#ifdef __cplusplus"):
            out.append("#if 0")
        elif line.startswith("#line"):
            continue
        elif not re.match(r"^(static|typedef|SQLITE_PRIVATE)", line):
            dm = decl_pattern.match(line)
            if dm and tail not in varonly_hdr:
                rettype, funcname, rest = dm.group(1), dm.group(2), dm.group(3)
                line = re.sub(r"^SQLITE_API ", "", line)
                rettype = re.sub(r"^SQLITE_API ", "", rettype)
                if re.match(r"^sqlite3[a-z]*_", funcname):
                    newline = "SQLITE_API " + rettype.strip()
                    if not rettype.endswith("*"):
                        newline += " "
                    newline += funcname + rest
                    out.append(("/* %s */" % newline) if funcname == "sqlite3_sourceid" else newline)
                else:
                    out.append("SQLITE_PRIVATE " + line)
            else:
                vm = VAR_PATTERN.match(line)
                if vm:
                    varname = vm.group(1)
                    line = re.sub(r"^SQLITE_API ", "", line)
                    if not re.match(r"^sqlite3_", varname) and not re.match(r"^sqlite3Show[A-Z]", varname):
                        out.append("SQLITE_PRIVATE " + re.sub(r"^extern ", "", line))
                    else:
                        if "const char sqlite3_version[];" in line:
                            line = "const char sqlite3_version[] = SQLITE_VERSION;"
                        out.append("SQLITE_API " + re.sub(r"^SQLITE_EXTERN ", "", line))
                elif re.match(r"^(SQLITE_EXTERN )?void \(\*sqlite3IoTrace\)", line):
                    out.append(re.sub(r"^SQLITE_EXTERN ", "", re.sub(r"^SQLITE_API ", "", line)))
                elif line.startswith("void (*sqlite3Os"):
                    out.append("SQLITE_PRIVATE " + re.sub(r"^SQLITE_API ", "", line))
                else:
                    out.append(line)
        else:
            out.append(line)
    section_comment("End of %s" % tail)


FLIST = """sqliteInt.h os_common.h ctime.c global.c status.c date.c os.c
fault.c mem0.c mem1.c mem2.c mem3.c mem5.c mutex.c mutex_noop.c mutex_unix.c
mutex_w32.c malloc.c printf.c treeview.c random.c threads.c utf.c util.c hash.c
opcodes.c os_kv.c os_unix.c os_win.c memdb.c bitvec.c pcache.c pcache1.c rowset.c
pager.c wal.c btmutex.c btree.c backup.c vdbemem.c vdbeaux.c vdbeapi.c vdbetrace.c
vdbe.c vdbeblob.c vdbesort.c vdbevtab.c memjournal.c walker.c resolve.c expr.c
alter.c analyze.c attach.c auth.c build.c callback.c delete.c func.c fkey.c
insert.c legacy.c loadext.c pragma.c prepare.c select.c table.c trigger.c update.c
upsert.c vacuum.c vtab.c wherecode.c whereexpr.c where.c window.c parse.c
tokenize.c complete.c main.c notify.c fts3.c fts3_aux.c fts3_expr.c fts3_hash.c
fts3_porter.c fts3_tokenizer.c fts3_tokenizer1.c fts3_tokenize_vtab.c fts3_write.c
fts3_snippet.c fts3_unicode.c fts3_unicode2.c json.c rtree.c icu.c fts3_icu.c
sqlite3rbu.c dbstat.c dbpage.c sqlite3session.c fts5.c stmt.c""".split()

for f in FLIST:
    p = os.path.join(SRCDIR, f)
    if not os.path.exists(p):
        sys.stderr.write("skip missing %s\n" % f)
        continue
    copy_file(p)

out.append("""/* Return the source-id for this library */
SQLITE_API const char *sqlite3_sourceid(void){ return SQLITE_SOURCE_ID; }""")
out.append("/************************** End of sqlite3.c ******************************/")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("sqlite3.c written: %d lines, version %s" % (len(out), version), file=sys.stderr)
