import os, shutil, subprocess, zipfile, time, tempfile
from pathlib import Path
root=Path.cwd()/"updater-diagnosis";root.mkdir()
subprocess.run(["gh","release","download","v1.0.7","--repo","Pawdroid/shadowrocket_for_desktop","--pattern","Shadowrocket-windows-64.zip","--dir",str(root)],check=True)
archive=root/"Shadowrocket-windows-64.zip"
env={k:v for k,v in os.environ.items() if k not in ("GH_TOKEN","GITHUB_TOKEN")}
for kind in ("same-directory","child-directory"):
    folder=root/kind;folder.mkdir()
    with zipfile.ZipFile(archive) as z:
        data=z.read("Shadowrocket-windows-64/AmazTool.exe")
    exe=folder/"AmazTool.exe";exe.write_bytes(data)
    destination=folder/"retired.exe" if kind=="same-directory" else folder/"child"/"retired.exe"
    destination.parent.mkdir(exist_ok=True)
    with (root/(kind+".log")).open("w") as log:
        process=subprocess.Popen([str(exe),str(root/"missing.zip")],cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT)
        time.sleep(2)
        try:
            exe.rename(destination);destination.rename(exe)
            print(kind,"running executable rename succeeded",file=log)
        except Exception as error:print(kind,repr(error),file=log)
        process.wait(timeout=30)
    print((root/(kind+".log")).read_text(encoding="utf-8",errors="replace"))
for mode in ("full","exclude-updater"):
    folder=root/mode;folder.mkdir()
    with zipfile.ZipFile(archive) as z:z.extractall(folder)
    installed=next(folder.iterdir());updater=installed/"AmazTool.exe"
    target=archive
    if mode=="exclude-updater":
        target=root/"without-updater.zip"
        with zipfile.ZipFile(archive) as z,zipfile.ZipFile(target,"w",compression=zipfile.ZIP_DEFLATED) as result:
            for entry in z.infolist():
                if entry.filename.endswith("/AmazTool.exe"):continue
                result.writestr(entry,z.read(entry.filename))
    with (root/(mode+".log")).open("w") as log:
        p=subprocess.Popen([str(updater),str(target)],cwd=installed,env=env,stdout=log,stderr=subprocess.STDOUT);p.wait(timeout=120)
        print("Exit",p.returncode,file=log)
    subprocess.run(["taskkill","/F","/IM","Shadowrocket.exe","/T"],capture_output=True)
    print((root/(mode+".log")).read_text(encoding="utf-8",errors="replace"))
