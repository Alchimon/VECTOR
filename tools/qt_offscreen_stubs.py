"""ТОЛЬКО ДЛЯ ТЕСТОВ В LINUX-ПЕСОЧНИЦЕ: заглушки libGL/libEGL/libxkbcommon/libdbus для offscreen-рендера Qt Quick
(QT_QPA_PLATFORM=offscreen, QT_QUICK_BACKEND=software) там, где системных библиотек нет.
Не входит в продукт и не поставляется. На Windows используются системные библиотеки/драйверы.

  python tools/qt_offscreen_stubs.py .venv/lib/python3.11/site-packages .venv/stubgl
  LD_LIBRARY_PATH=.venv/stubgl QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software pytest tests/ui -q

Важно: версии символов (version script) обязательны, иначе ld.so падает на check_match.
"""
import collections, glob, os, subprocess, sys
S=sys.argv[1]; OUT=sys.argv[2]
os.makedirs(OUT, exist_ok=True)
files=glob.glob(S+"/PySide6/Qt/lib/*.so*")+glob.glob(S+"/PySide6/Qt/plugins/**/*.so",recursive=True)+glob.glob(S+"/PySide6/Qt/qml/**/*.so",recursive=True)+glob.glob(S+"/PySide6/*.so*")
libs={"GL":(("gl","glX"),"libGL.so.1"),"EGL":(("egl",),"libEGL.so.1"),"xkbcommon":(("xkb_",),"libxkbcommon.so.0"),"dbus":(("dbus_",),"libdbus-1.so.3")}
syms=collections.defaultdict(dict)
for f in files:
    for line in subprocess.run(["nm","-D","--undefined-only",f],capture_output=True,text=True).stdout.splitlines():  # noqa: S603,S607 — фиксированный argv, nm из PATH разработчика
        sym=line.split()[-1]; name,_,ver=sym.partition("@"); ver=ver.lstrip("@") or None
        for lib,(pre,_) in libs.items():
            if name.startswith(pre) and not name.startswith("glib"):
                if ver or name not in syms[lib]: syms[lib][name]=ver
for lib,(_pre,soname) in libs.items():
    d=syms[lib]; byver=collections.defaultdict(list)
    for n,v in d.items(): byver[v or "VSTUB_BASE"].append(n)
    src=f"{OUT}/{lib}.c"; vs=f"{OUT}/{lib}.map"
    open(src,"w").write("".join(f"long {n}(void){{return 0;}}\n" for n in sorted(d)))
    open(vs,"w").write("\n".join(f"{v} {{ global: {'; '.join(sorted(ns))}; {'local: *;' if i==0 else ''} }};" for i,(v,ns) in enumerate(sorted(byver.items())))+"\n")
    cmd=["gcc","-shared","-fPIC","-o",f"{OUT}/{soname}",f"-Wl,-soname,{soname}"]+([f"-Wl,--version-script={vs}"] if any(v!="VSTUB_BASE" for v in byver) else [])+[src]
    subprocess.run(cmd,check=True); print(lib,{k:len(v) for k,v in byver.items()})  # noqa: S603,S607 — фиксированный argv, gcc из PATH разработчика
