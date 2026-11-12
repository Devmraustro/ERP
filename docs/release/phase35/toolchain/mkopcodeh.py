"""Python port of sqlite's tool/mkopcodeh.tcl.

The build image has no tclsh, so the amalgamation generators are ported to
Python instead of installing Tcl. Reads `parse.h` + `src/vdbe.c` on stdin and
writes `opcodes.h` on stdout, faithfully reproducing the TCL numbering
algorithm (TK_/OP_ aliasing, opcode groups, jump clustering).
"""
import re
import sys

lines = sys.stdin.read().splitlines()


def main():
    tk = {}
    paramused = {}
    synopsis = {}
    op = {}
    group = {}
    jump = {}
    in1 = {}
    in2 = {}
    in3 = {}
    out2 = {}
    out3 = {}
    ncycle = {}
    order = {}
    groups = {}
    used = {}
    sameas = {}
    def_ = {}
    nGroup = 0
    nOp = 0
    currentOp = ""
    prevName = ""

    for line in lines:
        m = re.match(r"#define TK_(\S+)\s+(\S+)", line)
        if m:
            tk["TK_" + m.group(1)] = m.group(2)
            continue

        if re.match(r"^.. Opcode: ", line):
            toks = line.split()
            currentOp = "OP_" + toks[2]
            bit = 0
            for term in toks:
                bit |= {"P1": 1, "P2": 2, "P3": 4, "P4": 8, "P5": 16}.get(term, 0)
            paramused[currentOp] = bit

        m = re.match(r"^.. Synopsis: (.*)", line)
        if m and currentOp != "":
            synopsis[currentOp] = m.group(1).strip()

        if line.startswith("case OP_"):
            toks = line.split()
            name = toks[1].strip(":")
            if name == "OP_Abortable":
                continue
            op[name] = -1
            for d in (group, jump, in1, in2, in3, out2, out3, ncycle):
                d[name] = 0
            i = 3
            while i < len(toks) - 1:
                term = toks[i].strip(",")
                if term == "same":
                    i += 1
                    if toks[i] == "as":
                        i += 1
                        sym = toks[i].strip(",")
                        val = int(tk[sym])
                        op[name] = val
                        used[val] = 1
                        sameas[val] = sym
                        def_.setdefault(val, name)
                elif term in ("group", "jump", "in1", "in2", "in3", "out2", "out3", "ncycle"):
                    {"group": group, "jump": jump, "in1": in1, "in2": in2,
                     "in3": in3, "out2": out2, "out3": out3, "ncycle": ncycle}[term][name] = 1
                i += 1
            if group[name]:
                newGroup = 0
                if nGroup in groups:
                    if prevName == "" or not group.get(prevName, 0):
                        newGroup = 1
                groups.setdefault(nGroup, []).append(name)
                if newGroup:
                    nGroup += 1
            else:
                if prevName != "" and group.get(prevName, 0):
                    nGroup += 1
            order[nOp] = name
            prevName = name
            nOp += 1

    out = []
    out.append("/* Automatically generated.  Do not edit */")
    out.append("/* See the tool/mkopcodeh.tcl script for details */")

    for name in ("OP_Noop", "OP_Explain", "OP_Abortable"):
        for d in (group, jump, in1, in2, in3, out2, out3, ncycle):
            d[name] = 0
        op[name] = -1
        order[nOp] = name
        nOp += 1

    rp2v_ops = {
        "OP_Transaction", "OP_AutoCommit", "OP_Savepoint", "OP_Checkpoint",
        "OP_Vacuum", "OP_JournalMode", "OP_VUpdate", "OP_VFilter", "OP_Init",
    }

    cnt = -1
    for i in range(nOp):
        name = order[i]
        if name in rp2v_ops:
            cnt += 1
            while cnt in used:
                cnt += 1
            op[name] = cnt
            used[cnt] = 1
            def_[cnt] = name

    for i in range(nOp):
        name = order[i]
        if op[name] >= 0 or not jump[name]:
            continue
        cnt += 1
        while cnt in used:
            cnt += 1
        op[name] = cnt
        used[cnt] = 1
        def_[cnt] = name

    mxJump = -1
    for i in range(nOp):
        name = order[i]
        if jump[name] and op[name] > mxJump:
            mxJump = op[name]

    for g in range(nGroup):
        gl = groups[g]
        ok = False
        seek = cnt
        while not ok:
            seek += 1
            while seek in used:
                seek += 1
            ok = True
            start = seek
            for _ in range(len(gl)):
                seek += 1
                if seek in used:
                    ok = False
                    break
        if ok:
            nxt = start
            for name in gl:
                if op[name] >= 0:
                    continue
                op[name] = nxt
                used[nxt] = 1
                def_[nxt] = name
                nxt += 1

    for i in range(nOp):
        name = order[i]
        if op[name] < 0:
            cnt += 1
            while cnt in used:
                cnt += 1
            op[name] = cnt
            used[cnt] = 1
            def_[cnt] = name

    mx = max(used.keys())
    for i in range(mx + 1):
        if i not in used:
            def_[i] = "OP_NotUsed_%d" % i
        name = def_[i]
        com = []
        if jump.get(name, 0):
            com.append("jump")
        if i in sameas:
            com.append("same as %s" % sameas[i])
        if name in synopsis:
            com.append("synopsis: %s" % synopsis[name])
        line = "#define %-16s %3d" % (name, i)
        if com:
            line += " /* %-42s */" % ", ".join(com)
        out.append(line)

    if mx > 255:
        raise SystemExit("More than 255 opcodes")

    bv = {}
    for i in range(mx + 1):
        x = 0
        name = def_[i]
        if not name.startswith("OP_NotUsed"):
            if jump.get(name, 0):
                x += 1
            if in1.get(name, 0):
                x += 2
            if in2.get(name, 0):
                x += 4
            if in3.get(name, 0):
                x += 8
            if out2.get(name, 0):
                x += 16
            if out3.get(name, 0):
                x += 32
            if ncycle.get(name, 0):
                x += 64
        bv[i] = x

    out.append("")
    out.append('/* Properties such as "out2" or "jump" that are specified in')
    out.append('** comments following the "case" for each opcode in the vdbe.c')
    out.append("** are encoded into bitvectors as follows:")
    out.append("*/")
    out.append("#define OPFLG_JUMP        0x01  /* jump:  P2 holds jmp target */")
    out.append("#define OPFLG_IN1         0x02  /* in1:   P1 is an input */")
    out.append("#define OPFLG_IN2         0x04  /* in2:   P2 is an input */")
    out.append("#define OPFLG_IN3         0x08  /* in3:   P3 is an input */")
    out.append("#define OPFLG_OUT2        0x10  /* out2:  P2 is an output */")
    out.append("#define OPFLG_OUT3        0x20  /* out3:  P3 is an output */")
    out.append("#define OPFLG_NCYCLE      0x40  /* ncycle:Cycles count against P1 */")
    out.append("#define OPFLG_INITIALIZER {" + chr(92))
    row = ""
    for i in range(mx + 1):
        if i % 8 == 0:
            row = "/* %3d */" % i
        row += " 0x%02x," % bv[i]
        if i % 8 == 7:
            out.append(row + chr(92))
            row = ""
    if row:
        out.append(row + "}")
    else:
        out.append("}")
    out.append("")
    out.append("/* The resolve3P2Values() routine is able to run faster if it knows")
    out.append("** the value of the largest JUMP opcode.  The smaller the maximum")
    out.append("** JUMP opcode the better, so the mkopcodeh.tcl script that")
    out.append("** generated this include file strives to group all JUMP opcodes")
    out.append("** together near the beginning of the list.")
    out.append("*/")
    out.append("#define SQLITE_MX_JUMP_OPCODE  %d  /* Maximum JUMP opcode */" % mxJump)

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
