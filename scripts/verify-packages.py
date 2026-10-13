import argparse, hashlib, json, os, platform, plistlib, shutil, subprocess, sys, time, zipfile
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--candidate',required=True)
parser.add_argument('--rid',required=True)
args=parser.parse_args()
system,arch=args.rid.split('-')
suffix='arm64' if arch=='arm64' else '64'
target={'win':'windows','osx':'macos','linux':'linux'}[system]
root=Path.cwd()/'package-verification';root.mkdir()
results={}
repo='Pawdroid/shadowrocket_for_desktop'
env={k:v for k,v in os.environ.items() if k not in ('GH_TOKEN','GITHUB_TOKEN','V2RAYN_LOCAL_APPLICATION_DATA_V2')}
def command(argv,**kwargs):
    result=subprocess.run(argv,text=True,encoding='utf-8',errors='replace',capture_output=True,**kwargs)
    if result.returncode:
        print(result.stdout[-6000:]);print(result.stderr[-6000:])
        raise subprocess.CalledProcessError(result.returncode,argv)
    return result
def download(tag,pattern):
    directory=root/tag;directory.mkdir(exist_ok=True)
    command(['gh','release','download',tag,'--repo',repo,'--pattern',pattern,'--dir',str(directory),'--skip-existing'])
    path=directory/pattern
    releases=json.loads(command(['gh','api',f'repos/{repo}/releases?per_page=30']).stdout)
    data=next(release for release in releases if release['tag_name']==tag)
    item=next(a for a in data['assets'] if a['name']==pattern)
    digest=item.get('digest')
    if digest and digest.startswith('sha256:'):
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest[7:],'Download digest mismatch'
    return path
def extract(archive,directory):
    directory.mkdir(exist_ok=True)
    if system=='osx':command(['ditto','-x','-k',str(archive),str(directory)])
    else:
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                path=(directory/entry.filename).resolve()
                assert path.is_relative_to(directory.resolve()),'Archive path escapes extraction root'
                z.extract(entry,directory)
                mode=(entry.external_attr>>16)&0o777
                if mode and system!='win' and path.is_file():path.chmod(mode)
    return next(directory.glob('*.app')) if system=='osx' else next(p for p in directory.iterdir() if p.is_dir())
def executable(installation,name):
    folder=installation/'Contents'/'MacOS' if system=='osx' else installation
    return folder/(name+'.exe' if system=='win' else name)
def stop(process):
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
def launch(installation,label,executable_path=None,extra_env=None):
    exe=executable_path or executable(installation,'Shadowrocket')
    log=(root/(label+'.log')).open('w')
    p=subprocess.Popen([str(exe)],cwd=exe.parent,env={**env,**(extra_env or {})},stdout=log,stderr=subprocess.STDOUT)
    try:
        time.sleep(15)
        assert p.poll() is None,f'{label}: GUI exited during startup'
        if system=='win':
            status=command(['powershell','-NoProfile','-Command',f'(Get-Process -Id {p.pid}).MainWindowHandle.ToInt64()']).stdout.strip()
            assert int(status)>0,f'{label}: no application window'
        elif system=='linux':
            window=command(['xdotool','search','--onlyvisible','--pid',str(p.pid)]).stdout.strip()
            assert window,f'{label}: no visible application window'
        results[label]='passed'
    finally:stop(p);log.close()
    return exe
def verify_macos(installation):
    command(['codesign','--verify','--deep','--strict',str(installation)])
    argv=['xcrun','stapler','validate',str(installation)]
    for attempt in range(3):
        validation=subprocess.run(argv,text=True,encoding='utf-8',errors='replace',capture_output=True)
        if validation.returncode==0:break
        output=validation.stdout+validation.stderr
        transient=any(error in output for error in ('NSURLErrorDomain Code=-1005','NSURLErrorDomain Code=-1001','NSURLErrorDomain Code=-1009'))
        if not transient or attempt==2:
            print(output)
            raise subprocess.CalledProcessError(validation.returncode,argv)
        print('Apple ticket validation network error; retrying')
        time.sleep(5*(attempt+1))
    command(['spctl','--assess','--type','execute',str(installation)])
    results['signature_and_notarization']='passed'

def stop_restarted_gui():
    time.sleep(5)
    if system=='win':
        command(['powershell','-NoProfile','-Command','if (-not (Get-Process Shadowrocket -ErrorAction SilentlyContinue)) { exit 1 }'])
        command(['taskkill','/F','/IM','Shadowrocket.exe','/T'])
    else:
        command(['pgrep','-x','Shadowrocket'])
        command(['pkill','-x','Shadowrocket'])
    time.sleep(2)

def upgrade(installation,helper,archive,label,expected_code=0):
    updater=executable(installation,helper)
    with (root/(label+'.log')).open('w') as log:
        p=subprocess.Popen([str(updater),str(archive)],cwd=updater.parent,env=env,stdout=log,stderr=subprocess.STDOUT)
        p.wait(timeout=120)
        assert p.returncode==expected_code,f'{label}: updater returned {p.returncode}'

def verify_payload(archive,installation):
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            if entry.is_dir():continue
            relative=entry.filename.split('/',1)[1]
            installed=installation/relative
            assert installed.is_file(),f'Upgrade missed {relative}'
            assert hashlib.sha256(installed.read_bytes()).digest()==hashlib.sha256(package.read(entry)).digest(),f'Upgrade did not replace {relative}'

try:
    name=f'Shadowrocket-{target}-{suffix}.zip'
    candidate=download(args.candidate,name)
    current=extract(candidate,root/'fresh-install')
    assert platform.machine().lower() in ({'arm64','aarch64'} if arch=='arm64' else {'amd64','x86_64'}),'Wrong runner architecture'
    if system=='osx':
        verify_macos(current)
        image=download(args.candidate,f'Shadowrocket-macos-{suffix}.dmg')
        command(['hdiutil','verify',str(image)])
        mount=root/'mounted-dmg';mount.mkdir()
        command(['hdiutil','attach',str(image),'-readonly','-nobrowse','-mountpoint',str(mount)])
        try:
            installed=root/'dmg-install'/'Shadowrocket.app';installed.parent.mkdir()
            command(['ditto',str(mount/'Shadowrocket.app'),str(installed)])
            verify_macos(installed)
            launch(installed,'dmg_installed_gui_start')
        finally:command(['hdiutil','detach',str(mount)])
        results['dmg_install']='passed'

    core=current/'Contents'/'Resources'/'bin'/'xray'/'xray' if system=='osx' else current/'bin'/'xray'/('xray.exe' if system=='win' else 'xray')
    assert '26.7.28' in command([str(core),'version'],env=env).stdout
    results['native_core_start']='passed'
    launch(current,'fresh_gui_start')
    baseline=download('v1.0.6',name)
    old=extract(baseline,root/'upgrade-install')
    launch(old,'baseline_gui_start')
    if system=='osx':
        data=Path.home()/'Library'/'Application Support'/'Shadowrocket'/'guiConfigs'
    else:data=old/'guiConfigs'
    data.mkdir(parents=True,exist_ok=True)
    sentinel=data/'upgrade-verification-sentinel.txt';sentinel.write_text('preserve-existing-user-data')
    upgrade(old,'AmazTool',candidate,'upgrade')
    verify_payload(candidate,old)
    expected=executable(current,'Shadowrocket')
    actual=executable(old,'Shadowrocket')
    assert hashlib.sha256(actual.read_bytes()).digest()==hashlib.sha256(expected.read_bytes()).digest(),'Updater did not install the candidate binary'
    assert sentinel.read_text()=='preserve-existing-user-data','Upgrade lost user data'
    stop_restarted_gui()
    if system=='osx':verify_macos(old)
    launch(old,'upgraded_gui_start')
    results['upgrade_from_1_0_6']='passed'
    helper='AmazTool' if system=='win' else 'ShadowrocketUpdater'
    upgrade(old,helper,candidate,'current_updater')
    verify_payload(candidate,old)
    stop_restarted_gui()
    assert sentinel.read_text()=='preserve-existing-user-data','Current updater lost user data'
    if system=='osx':verify_macos(old)
    launch(old,'current_updater_gui_start')
    results['current_updater_install_and_restart']='passed'
    upgrade(old,helper,root/'missing-archive.zip','updater_missing_archive',expected_code=1)
    results['updater_failure_exit']='passed'
    # Exercise different single-file bundle offsets without changing the signed app:
    # the current helper installs a portable fixture containing the legacy helper.
    fixture=root/'updater-self-replacement';fixture.mkdir()
    source=executable(current,helper)
    running=executable(fixture,helper) if system!='osx' else fixture/helper
    shutil.copy2(source,running)
    running.chmod(0o755)
    legacy_member='Shadowrocket.app/Contents/MacOS/AmazTool' if system=='osx' else f'Shadowrocket-{target}-{suffix}/AmazTool'+('.exe' if system=='win' else '')
    with zipfile.ZipFile(baseline) as package:legacy_bytes=package.read(legacy_member)
    different_bundle=root/'different-updater-bundle.zip'
    with zipfile.ZipFile(different_bundle,'w',compression=zipfile.ZIP_DEFLATED) as package:
        for filename,content in [('Shadowrocket'+('.exe' if system=='win' else ''),core.read_bytes()),(running.name,legacy_bytes)]:
            entry=zipfile.ZipInfo('fixture/'+filename);entry.external_attr=0o100755<<16
            package.writestr(entry,content)
    # This fixture intentionally lives outside a .app, so invoke its helper directly.
    with (root/'updater_self_replacement.log').open('w') as log:
        p=subprocess.Popen([str(running),str(different_bundle)],cwd=fixture,env=env,stdout=log,stderr=subprocess.STDOUT)
        p.wait(timeout=120)
        assert p.returncode==0,'Current updater failed when its bundle layout changed'
    assert running.read_bytes()==legacy_bytes,'Fixture updater was not replaced'
    results['updater_self_replacement']='passed'
    if system=='linux':
        for tag in ('v1.0.6',args.candidate):
            deb=download(tag,f'Shadowrocket-linux-{suffix}.deb')
            command(['sudo','apt-get','install','-y',str(deb)])
        version=command(['dpkg-query','-W','-f=${Version}','shadowrocket']).stdout
        assert version.strip()==args.candidate.removeprefix('v'),'Installed Debian version mismatch'
        core=Path('/opt/shadowrocket/bin/xray/xray')
        assert os.access(core,os.X_OK),'Installed core is not executable'
        missing_user_data=root/'new-user-data'
        assert not missing_user_data.exists()
        launch(Path('/opt/shadowrocket'),'debian_installed_gui_start',Path('/usr/bin/shadowrocket'),{'XDG_DATA_HOME':str(missing_user_data)})
        assert (missing_user_data/'Shadowrocket'/'guiLogs').is_dir(),'Debian install did not create the user log directory'
        results['debian_install_and_package_upgrade']='passed'
    results['status']='passed'
finally:
    (root/'results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))
