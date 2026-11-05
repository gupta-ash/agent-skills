"""Local owned-surface installer. Check plans; Verify only reads retained closure."""
from __future__ import annotations
import argparse
import base64
import ctypes
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile

COMPONENT = "agent-skills"
REPOSITORY = "gupta-ash/agent-skills"
PRIVATE = COMPONENT == "skills-private"
PREFIX = "ash_" if PRIVATE else ""
NAMESPACE = "skills-runtime" if PRIVATE else "agent-skills"
RECEIPT_FIELDS = {"schema_version","component","repository","version","source_commit","archive_sha256","parameters","files","configuration_paths","held_paths","last_code_change_utc"}
MANIFEST_FIELDS = {"schema_version","component","repository","version","source_commit","files"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
RESERVED = {"CON","PRN","AUX","NUL",*(f"COM{i}" for i in range(1,10)),*(f"LPT{i}" for i in range(1,10))}

class Refusal(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise Refusal(message)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def data_json(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode("utf-8")

def load(path):
    def pairs(rows):
        value={}
        for k,v in rows:
            require(k not in value,"duplicate JSON key")
            value[k]=v
        return value
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),object_pairs_hook=pairs)

def rel(value):
    require(isinstance(value,str) and value and "\\" not in value and ":" not in value,"unsafe relative path")
    p=PurePosixPath(value)
    require(not p.is_absolute() and str(p)==value and all(x not in ("", ".", "..") and not x.endswith(("."," ")) and x.split(".")[0].upper() not in RESERVED for x in value.split("/")),"unsafe relative path")
    return value

def absolute(value):
    p=Path(value)
    require(p.is_absolute() and not str(p).startswith(("\\\\","//")),"path must be absolute and local")
    require(".." not in p.parts and all(not x.endswith(("."," ")) and x.split(".")[0].upper() not in RESERVED for x in p.parts if x != p.anchor),"unsafe absolute path")
    return Path(os.path.abspath(p))

def key(path):
    return os.path.normcase(os.path.abspath(path)).casefold()

def under(path,root):
    return key(path)==key(root) or key(path).startswith(key(root)+os.sep)

def unlinked(path, *, allow_missing=True):
    for p in (path,*path.parents):
        if p.exists() or p.is_symlink():
            st=p.lstat()
            require(not stat.S_ISLNK(st.st_mode) and not getattr(st,"st_file_attributes",0)&0x400,f"link/reparse path refused: {p}")
            if p==path:
                require(not p.is_file() or st.st_nlink==1,f"hardlinked target refused: {p}")
    require(allow_missing or path.is_file(),f"required file is missing: {path}")

def actual_profile():
    if os.name=="nt":
        buf=ctypes.create_unicode_buffer(32768)
        require(ctypes.windll.shell32.SHGetFolderPathW(None,40,None,0,buf)==0,"cannot resolve actual account profile")
        result=absolute(buf.value)
        require(key(os.environ.get("USERPROFILE",""))==key(result),"USERPROFILE disagrees with actual account")
        return result
    import pwd
    return absolute(pwd.getpwuid(os.getuid()).pw_dir)

def powershell_path(script,path):
    # Bind the path as data and use the native host's matching built-in modules.
    # A PowerShell 7 parent can otherwise make Windows PowerShell load incompatible modules.
    exe=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    child_env=dict(os.environ,PSModulePath=str(exe.parent/'Modules'),PSModuleAnalysisCachePath=os.devnull)
    expression="$ErrorActionPreference='Stop';$p='"+str(path).replace("'","''")+"';"+script
    encoded=base64.b64encode(expression.encode('utf-16-le')).decode('ascii')
    return subprocess.run([str(exe),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],env=child_env,capture_output=True,timeout=30)

def private_directory(path):
    if os.name=="nt":
        script="$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$a=Get-Acl -LiteralPath $p;if($a.Owner -ne $sid -and ([Security.Principal.NTAccount]$a.Owner).Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid){exit 9};$a.SetAccessRuleProtection($true,$false);foreach($old in @($a.Access)){[void]$a.RemoveAccessRuleSpecific($old)};foreach($s in @($sid,'S-1-5-18','S-1-5-32-544')){$identity=New-Object Security.Principal.SecurityIdentifier($s);$r=New-Object Security.AccessControl.FileSystemAccessRule($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow');$a.AddAccessRule($r)};Set-Acl -LiteralPath $p -AclObject $a"
        proc=powershell_path(script,path)
        require(proc.returncode==0,"cannot establish private preservation ACL")
    else:
        require(path.stat().st_uid==os.getuid(),"directory is not owned by current user")
        path.chmod(0o700)

def owned_directory(path):
    unlinked(path)
    require(path.is_dir(),f"owned directory prerequisite: {path}")
    if os.name=="nt":
        script="$o=(Get-Acl -LiteralPath $p).Owner;$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;if($o -ne $sid -and ([Security.Principal.NTAccount]$o).Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid){exit 9}"
        proc=powershell_path(script,path)
        require(proc.returncode==0,f"directory must be owned by current account: {path}")
    else:
        require(path.stat().st_uid==os.getuid(),"directory is not owned by current account")

def manifest_validate(m):
    require(isinstance(m,dict) and set(m)==MANIFEST_FIELDS and type(m['schema_version']) is int and m['schema_version']==1,"invalid manifest schema")
    require(m['component']==COMPONENT and m['repository']==REPOSITORY and TAG.fullmatch(m['version']) and HEX40.fullmatch(m['source_commit']),"wrong package identity")
    seen=set()
    require(isinstance(m['files'],list) and m['files'],"manifest files missing")
    for row in m['files']:
        require(set(row)=={'path','sha256','mode'} and HEX64.fullmatch(row['sha256']) and row['mode'] in ('100644','100755'),"invalid manifest file row")
        p=rel(row['path']); require(p.casefold() not in seen,"duplicate/case-colliding manifest member");seen.add(p.casefold())

def package_validate(root,archive_path,expected_sha,source_commit,version,frontdoor):
    require(HEX64.fullmatch(expected_sha or '') and HEX40.fullmatch(source_commit or ''),"archive digest and dereferenced commit required")
    require(sha(archive_path.read_bytes())==expected_sha,"archive checksum mismatch")
    m=load(root/'manifest.json');manifest_validate(m)
    require(m['version']==version and m['source_commit']==source_commit,"tag/source OID mismatch")
    expected={row['path']:row for row in m['files']}
    require({'install.ps1','deploy/install.py','deploy/exports.json'}<=expected.keys(),"installer input closure missing")
    with zipfile.ZipFile(archive_path) as z:
        seen=set()
        for member in z.infolist():
            name=rel(member.filename)
            require(name.casefold() not in seen and not member.is_dir(),"extra/duplicate archive member");seen.add(name.casefold())
            mode=member.external_attr>>16
            require(not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0,stat.S_IFREG),"archive link/special member refused")
            require(name=='manifest.json' or name in expected,"undeclared archive member")
            content=z.read(member)
            if name in expected:
                require(sha(content)==expected[name]['sha256'],"archive member hash mismatch")
                require((mode&0o111 != 0)==(expected[name]['mode']=='100755'),"archive member mode mismatch")
            unlinked(root/name,allow_missing=False)
            require((root/name).read_bytes()==content,"extracted package differs from archive")
        require(seen=={x.casefold() for x in [*expected,'manifest.json']},"incomplete archive membership")
    require(sha(frontdoor.read_bytes())==expected['install.ps1']['sha256'],"executing frontdoor differs from selected release; download its install.ps1")
    return m

def validate_receipt(r,home):
    require(isinstance(r,dict) and set(r)==RECEIPT_FIELDS and type(r['schema_version']) is int and r['schema_version']==1,"invalid installed receipt")
    require(r['component']==COMPONENT and r['repository']==REPOSITORY and TAG.fullmatch(r['version']) and HEX40.fullmatch(r['source_commit']) and HEX64.fullmatch(r['archive_sha256']),"invalid installed identity")
    p=r['parameters'];require(set(p)<={'Harness','Skills','PythonExe','UserRoot','RuntimeConfigDir'} and {'Harness','Skills','PythonExe','UserRoot'}<=set(p),"invalid retained parameters")
    require(key(absolute(p['UserRoot']))==key(home),"installed root/account mismatch")
    absolute(p['PythonExe'])
    if 'RuntimeConfigDir' in p:require(key(absolute(p['RuntimeConfigDir']))==key(home/'.agents'/NAMESPACE),'retained runtime namespace mismatch')
    require(isinstance(p['Harness'],list) and p['Harness'] and set(p['Harness'])<={'claude','codex','omp'} and len(set(p['Harness']))==len(p['Harness']),"invalid retained Harness")
    require(isinstance(p['Skills'],list) and p['Skills'] and len(set(p['Skills']))==len(p['Skills']),"invalid retained Skills")
    require(isinstance(r['files'],list) and isinstance(r['configuration_paths'],list) and isinstance(r['held_paths'],list),"invalid installed populations")
    seen=set()
    for row in r['files']:
        require(set(row)=={'path','sha256','mode'} and HEX64.fullmatch(row['sha256']) and row['mode'] in ('100644','100755'),"invalid installed file")
        path=absolute(row['path']);require(allowed(path,home) and key(path) not in seen,"installed file outside declared product roots or duplicate");seen.add(key(path))
    held_seen=set()
    for row in r['held_paths']:
        require(set(row)=={'path','reason'} and isinstance(row['reason'],str) and row['reason'] and allowed(absolute(row['path']),home),"invalid held path")
        require(key(row['path']) not in seen and key(row['path']) not in held_seen,"held paths cannot be installed files or duplicate")
        held_seen.add(key(row['path']))
    for path in r['configuration_paths']:
        require(key(path) in {key(home/'.omp/agent/config.yml'),key(home/'.agents'/NAMESPACE/'runtime.json'),key(home/'.agents'/NAMESPACE/'omp-install.json')},"undeclared configuration path")
    stamp=r['last_code_change_utc']
    if stamp is not None:
        require(isinstance(stamp,str) and stamp.endswith('Z'),"invalid code timestamp")
        datetime.fromisoformat(stamp.replace('Z','+00:00'))
    return r

def allowed(path,home):
    roots=[home/'.agents/skills',home/'.agents/installations'/COMPONENT,home/'.agents'/NAMESPACE,home/'.claude/skills',home/'.claude/agents',home/'.omp/shared/extensions'/name('discriminator'),home/'.omp/agent/skills'/('ash_omp_web' if PRIVATE else 'omp-web')]
    globals_={key(home/'.agents'/('ash_save_artifacts.md' if PRIVATE else 'save_artifacts.md')),key(home/'.agents'/('ash_save_helpers.md' if PRIVATE else 'save_helpers.md'))}
    return key(path) in globals_ or any(under(path,p) for p in roots)

def name(base):
    if PRIVATE:
        return 'ash_'+base.replace('-','_')
    return base

def selected_entries(declaration,skills,harness):
    require(set(declaration)=={'schema_version','component','entries'} and declaration['schema_version']==1,"invalid exports")
    all_fronts={r['destination'].split('/')[3] for r in declaration['entries'] if r['destination'].startswith('harness/.agents/skills/') and r['destination'].endswith('/SKILL.md')}
    require(set(skills)<=all_fronts,"unknown Skills selection: "+', '.join(sorted(set(skills)-all_fronts)))
    closure=set(skills)
    if name('restore') in closure:closure.add(name('save'))
    wiki=any(x==name('wiki') or x.startswith(name('wiki')+('_' if PRIVATE else '-')) for x in closure)
    if wiki:closure.update((name('wiki'),name('obsidian'),name('discriminator')))
    if 'omp' in harness and closure & {name('webfetch'),name('websearch')}:
        closure.update((name('webfetch'),name('websearch')))
    result=[]
    for row in declaration['entries']:
        require(set(row)=={'source','destination'},"invalid export row")
        d=rel(row['destination']);rel(row['source'])
        require(d.startswith('harness/.'),"invalid home projection")
        if d.startswith('harness/.agents/skills/'):
            component=d.split('/')[3]
            include=component in closure or component=='_workflow' and wiki or component=='_load_env.sh' and PRIVATE and name('hoopla-ebook-download') in closure
        elif d.startswith('harness/.agents/'):
            include=bool(closure & {name('save'),name('restore'),name('save-migration')})
        elif d.startswith('harness/.claude/'):
            include='claude' in harness and name('discriminator') in closure
        elif d.startswith('harness/.omp/shared/extensions/'):
            include='omp' in harness and name('discriminator') in closure
        elif d.startswith('harness/.omp/agent/skills/'):
            include='omp' in harness and bool(closure & {name('webfetch'),name('websearch')})
        else:
            raise Refusal('undeclared export destination: '+d)
        if include:result.append(row)
    if name('discriminator') in closure:
        count=sum(r['destination'].startswith('harness/.agents/skills/'+name('discriminator')+'/') for r in result)
        require(count==27,'complete discriminator 27-runtime-file closure required')
    return result,sorted(closure),wiki

def policy(home,existing):
    if not PRIVATE:return []
    path=home/'.agents/workstation-config/install-policy.json'
    if not path.exists():
        require(not existing,'existing private skills require workstation-config install-policy.json; holds cannot be inferred or released')
        return []
    unlinked(path,allow_missing=False);p=load(path)
    require(set(p)=={'schema_version','machine','userprofile','deploy_exclude'} and type(p['schema_version']) is int and p['schema_version']==1,'invalid private install policy')
    require(isinstance(p['machine'],str) and p['machine'].casefold()==socket.gethostname().casefold() and key(absolute(p['userprofile']))==key(home),'private policy host/profile mismatch')
    require(isinstance(p['deploy_exclude'],list),'invalid deploy_exclude')
    seen=set()
    for row in p['deploy_exclude']:
        require(set(row)=={'path','reason','expires','added'} and all(isinstance(v,str) and v for v in row.values()),'invalid held policy row')
        rel(row['path']);require(row['path'].casefold() not in seen,'duplicate held policy path');seen.add(row['path'].casefold())
        added=datetime.strptime(row['added'],'%Y-%m-%d').date()
        require(added.isoformat()==row['added'] and added<=datetime.now().date(),'invalid held added date')
    return p['deploy_exclude']

def hold_reason(destination,holds):
    for row in holds:
        pattern=row['path']
        if fnmatch.fnmatchcase(destination,pattern) or pattern.endswith('/**') and destination==pattern[:-3]:return row['reason']
    return None

def runtime_validate(path,home):
    obj=load(path)
    require(isinstance(obj,dict),'runtime.json must be an object')
    def inspect(value):
        if isinstance(value,dict):
            require(not {'capability_receipt_path','qualification_receipt_path'} & set(value),'fleet certification fields are rejected, not ignored')
            for v in value.values():inspect(v)
        elif isinstance(value,list):
            for v in value:inspect(v)
    inspect(obj)
    require(isinstance(obj.get('machine'),str) and obj['machine'].casefold()==socket.gethostname().casefold(),'runtime machine mismatch')
    tokens=obj.get('tokens',{});require(isinstance(tokens,dict) and key(absolute(tokens.get('__USERPROFILE__','')))==key(home),'runtime account/profile mismatch')
    python=absolute(tokens.get('__OMP_PYTHON__',''));unlinked(python,allow_missing=False)
    require(isinstance(obj.get('workflow_native'),dict),'runtime workflow_native declaration required')
    routes=obj['workflow_native']
    require(set(routes)<={'omp','claude','codex'},'unknown native backend declaration')
    for backend,route in routes.items():
        fields={'model','agent_dir','config_files','review_extension'} if backend=='omp' else {'executable','private_state_roots','model','task_enabled','windows_sandbox'} if backend=='codex' else {'executable','private_state_roots','model','proxy_url'}
        require(isinstance(route,dict) and set(route)<=fields,'native backend has unknown fields: '+backend)
        if backend!='omp':
            absolute(route.get('executable',''))
            require(isinstance(route.get('private_state_roots'),list) and route['private_state_roots'],'native private-state exclusions are required')
            for value in route['private_state_roots']:absolute(value)
            if backend=='codex':require(route.get('task_enabled',False) is False,'Codex task execution remains disabled')
        if 'agent_dir' in route:absolute(route['agent_dir'])
        if 'config_files' in route:
            require(isinstance(route['config_files'],list) and route['config_files'],'config_files must be nonempty')
            for value in route['config_files']:unlinked(absolute(value),allow_missing=False)
        if 'model' in route:require(isinstance(route['model'],str) and route['model'],'model must be explicit or absent')
        if 'review_extension' in route:
            ref=route['review_extension']
            require(isinstance(ref,dict) and set(ref)=={'path','sha256','media_type'} and HEX64.fullmatch(ref['sha256']) and ref['media_type']=='text/typescript','closed extension InputRef required')
            require(key(ref['path'])==key(home/'.omp/shared/extensions'/name('discriminator')/'index.ts'),'review extension must belong to this installed distribution')
    return obj

def omp_manifest(path):
    manifest=load(path)
    require(isinstance(manifest,dict) and set(manifest)=={'schema','package','package_tree_relative','executable_relative','payload_relative','installer'} and type(manifest['schema']) is int and manifest['schema']==1 and manifest['package']=='@oh-my-pi/pi-coding-agent','invalid OMP installation manifest')
    require(isinstance(manifest['installer'],dict) and set(manifest['installer'])=={'runtime_relative'},'closed OMP Bun runtime declaration required')
    for field in ('package_tree_relative','executable_relative','payload_relative'):rel(manifest[field])
    rel(manifest['installer']['runtime_relative'])
    require(PurePosixPath(manifest['payload_relative']).is_relative_to(PurePosixPath(manifest['package_tree_relative'])),'OMP payload must remain in its package tree')
    return manifest

def omp_package_input(path,package_root):
    """Admit existing SDK data without treating package-manager hardlinks as writes.

    Native admission binds and rechecks the bytes before loading the SDK. This
    boundary only reads contained regular package inputs; owned write targets
    and selected executables continue through the strict single-link guard.
    """
    path=absolute(path);package_root=absolute(package_root)
    require(under(path,package_root) and key(path)!=key(package_root),'OMP package input escapes the declared package tree')
    for candidate in (path,*path.parents):
        metadata=candidate.lstat()
        require(not stat.S_ISLNK(metadata.st_mode) and not getattr(metadata,'st_file_attributes',0)&0x400,'OMP package input crosses a link/reparse path: '+str(candidate))
        require(stat.S_ISREG(metadata.st_mode) if candidate==path else stat.S_ISDIR(metadata.st_mode),'OMP package input is not a regular contained file: '+str(candidate))
    return path

def omp_executable(runtime,namespace):
    if runtime is not None:
        managed=runtime.get('omp_managed',{})
        require(managed.get('enabled') is True,'selected OMP requires omp_managed.enabled')
        root=absolute(managed.get('install_root',''));unlinked(root)
        require(namespace is not None,'omp-install.json is a named prerequisite for the configured OMP route')
        manifest=omp_manifest(namespace/'omp-install.json')
        runtime_path=root/rel(manifest['installer']['runtime_relative']);unlinked(runtime_path,allow_missing=False)
        exe=root/manifest['executable_relative'];unlinked(exe,allow_missing=False)
        package_root=root/manifest['package_tree_relative'];unlinked(package_root)
        omp_package_input(root/manifest['payload_relative'],package_root)
        package_identity=omp_package_input(package_root/'package.json',package_root)
        require(load(package_identity).get('name')==manifest['package'],'OMP installed package identity mismatch')
        return exe
    found=shutil.which('omp')
    require(found is not None,'OMP executable prerequisite: install OMP separately')
    exe=absolute(found);unlinked(exe,allow_missing=False)
    return exe

def omp_projection(home,work,exe,extensions):
    try:import yaml
    except ImportError as exc:raise Refusal('selected OMP configuration requires PyYAML in the selected existing Python') from exc
    target=home/'.omp/agent/config.yml';unlinked(target)
    agent=work/'omp-projection'/uuid.uuid4().hex;unlinked(agent);agent.mkdir(parents=True)
    if target.exists():shutil.copy2(target,agent/'config.yml')
    env=dict(os.environ,PI_CODING_AGENT_DIR=str(agent))
    for field in ('OMP_PROFILE','PI_PROFILE','PI_CONFIG_FILES','CLAUDE_CONFIG_DIR','NODE_OPTIONS','BUN_OPTIONS','PI_CONFIG_PATH','PI_CONFIG_DIR','PI_CONFIG_PATHS','PI_DEFAULT_ENV','PI_DEFAULT_ENV_FILE','PI_DEFAULT_ENV_FILES','PI_CODING_AGENT_CONFIG','OMP_CONFIG_PATH'):
        env.pop(field,None)
    contributions={'skills.customDirectories':[str(home/'.agents/skills')]}
    if extensions:contributions['extensions']=[str(p) for p in extensions]
    for field,items in contributions.items():
        def cli(*args):
            p=subprocess.run([str(exe),'config',*args,'--json'],cwd=agent,env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=30)
            require(p.returncode==0,'OMP public config API rejected '+field)
            result=json.loads(p.stdout);require(result.get('key')==field,'OMP public config result mismatch');return result.get('value')
        old=cli('get',field)
        require(old is None or isinstance(old,list) and all(isinstance(x,str) for x in old),'OMP setting must be an array: '+field)
        if field=='extensions':
            other='ash_discriminator' if not PRIVATE else 'discriminator'
            require(not any('/extensions/'+other+'/' in x.replace('\\','/') for x in old or []),'opposite distribution discriminator registration collision')
        value=list(dict.fromkeys([*(old or []),*items]))
        require(cli('set',field,json.dumps(value,separators=(',',':')))==value,'OMP configuration read-back mismatch')
    output=(agent/'config.yml').read_bytes()
    try:document=yaml.safe_load(output) or {}
    except yaml.YAMLError:raise Refusal('OMP projected configuration is malformed; private content is not printed') from None
    verify_omp_document(document,contributions)
    return target,output,contributions

def verify_omp_document(document,contributions):
    for field,expected in contributions.items():
        value=document
        for segment in field.split('.'):
            value=value.get(segment) if isinstance(value,dict) else None
        if value is None and isinstance(document,dict):value=document.get(field)
        require(isinstance(value,list) and all(x in value for x in expected),'OMP managed setting drift: '+field)

def atomic(path,content,mode='100644'):
    unlinked(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    unlinked(path)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.chmod(tmp,0o755 if mode=='100755' else 0o644)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def configuration_verify(home,install,r):
    spec=load(install/'configuration.json')
    require(set(spec)=={'runtime_files','omp','policy_required'} and type(spec['policy_required']) is bool,'invalid retained configuration closure')
    namespace=home/'.agents'/NAMESPACE
    for filename,expected in spec['runtime_files'].items():
        require(filename in ('runtime.json','omp-install.json') and HEX64.fullmatch(expected),'invalid runtime configuration reference')
        path=namespace/filename;unlinked(path,allow_missing=False)
        require(sha(path.read_bytes())==expected,'local runtime configuration drift; explicit reviewed configuration change required')
    if 'runtime.json' in spec['runtime_files']:runtime_validate(namespace/'runtime.json',home)
    if spec['omp']:
        try:import yaml
        except ImportError as exc:raise Refusal('Verify of OMP configuration requires PyYAML') from exc
        path=home/'.omp/agent/config.yml';unlinked(path,allow_missing=False)
        try:document=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except yaml.YAMLError:raise Refusal('OMP installed configuration is malformed; private content is not printed') from None
        verify_omp_document(document,spec['omp'])
    require(set(map(key,r['configuration_paths']))=={key(namespace/x) for x in spec['runtime_files']} | ({key(home/'.omp/agent/config.yml')} if spec['omp'] else set()),'receipt configuration closure mismatch')

def retained_projection(home,install,r,m):
    declaration=load(install/'exports.json')
    rows,closure,_=selected_entries(declaration,r['parameters']['Skills'],r['parameters']['Harness'])
    manifest_files={row['path']:row for row in m['files']}
    require(sha((install/'exports.json').read_bytes())==manifest_files['deploy/exports.json']['sha256'],'retained exports are not source-bound')
    holds=policy(home,load(install/'configuration.json')['policy_required'])
    expected_held={}
    held={key(x['path']):x['reason'] for x in r['held_paths']}
    expected={}
    native=home/'.claude/skills'
    shared='claude' in r['parameters']['Harness'] and (native.is_symlink() or native.exists() and getattr(native.lstat(),'st_file_attributes',0)&0x400)
    if shared:
        require(key(native.resolve(strict=True))==key((home/'.agents/skills').resolve(strict=True)),'Claude junction target changed')
    for row in rows:
        relative=row['destination'].removeprefix('harness/')
        source=manifest_files.get('payload/'+relative)
        require(source is not None,'retained source projection is incomplete')
        paths=[home/relative]
        if 'claude' in r['parameters']['Harness'] and not shared and relative.startswith('.agents/skills/') and relative.endswith('/SKILL.md'):
            paths.append(native/Path(relative).relative_to('.agents/skills'))
        reason=hold_reason(row['destination'],holds)
        for path in paths:
            if reason:expected_held[key(path)]=reason
            else:expected[key(path)]=(source['sha256'],source['mode'])
    if PRIVATE and expected_held:
        for front in ('ash_save','ash_restore'):
            path=home/'.agents/skills'/front/'SKILL.md'
            legacy=home/'.agents/skills/ash_relay_name'
            if key(path) in expected_held and path.is_file() and legacy.exists() and 'ash_relay_name' in path.read_text(encoding='utf-8-sig'):
                expected_held[key(legacy)]='EOL-pending: required by held '+front+' caller; no hold release is authorized'
                break
    require(held==expected_held,'held population differs from the actual matched private install policy')
    for target,source in (('install.ps1','install.ps1'),('install.py','deploy/install.py'),('exports.json','deploy/exports.json'),('retired.json','deploy/retired.json')):
        require(source in manifest_files,'retained verifier input missing from package')
        expected[key(install/target)]=(manifest_files[source]['sha256'],manifest_files[source]['mode'])
    for legal in ('LICENSE','NOTICE'):
        if legal in manifest_files:expected[key(install/legal)]=(manifest_files[legal]['sha256'],manifest_files[legal]['mode'])
    expected[key(install/'manifest.json')]=(sha((install/'manifest.json').read_bytes()),'100644')
    expected[key(install/'configuration.json')]=(sha((install/'configuration.json').read_bytes()),'100644')
    observed={key(x['path']):(x['sha256'],x['mode']) for x in r['files']}
    require(observed==expected,'receipt files do not match the complete selected source projection')

def verify(home,install,r,version):
    require(r['version']==version,'expected version mismatch')
    m=load(install/'manifest.json');manifest_validate(m)
    require(all(m[k]==r[k] for k in ('component','repository','version','source_commit')),'retained manifest identity mismatch')
    require(r['files'],'empty installed ownership population')
    for row in r['files']:
        path=absolute(row['path']);unlinked(path,allow_missing=False)
        require(sha(path.read_bytes())==row['sha256'],'immutable installed file drift: '+str(path))
        if os.name!='nt':require(bool(path.stat().st_mode&0o111)==(row['mode']=='100755'),'installed file mode drift: '+str(path))
    required={key(install/x) for x in ('install.ps1','install.py','exports.json','retired.json','manifest.json','configuration.json')}
    require(required<={key(x['path']) for x in r['files']},'retained verifier closure incomplete')
    retained_projection(home,install,r,m)
    configuration_verify(home,install,r)
    # A retained matched policy cannot disappear; pristine installs record no legacy takeover.
    policy(home,load(install/'configuration.json')['policy_required'])
    return {'status':'verified','component':COMPONENT,'version':version,'files':len(r['files']),'held_paths':r['held_paths']}

def execute(a):
    require(TAG.fullmatch(a.version),'Version must be exact vMAJOR.MINOR.PATCH')
    require(sys.version_info>=(3,11),'PythonExe prerequisite: Python 3.11+ is required')
    require(sys.prefix==sys.base_prefix,'PythonExe must select an existing base interpreter, not a component venv')
    home=absolute(a.user_root) if a.user_root else actual_profile()
    require(key(home)==key(actual_profile()),'UserRoot must be the actual invoking account profile')
    owned_directory(home)
    work=absolute(a.work_root);unlinked(work)
    require(not any(under(work,p) or under(p,work) for p in (home/'.agents',home/'.claude',home/'.omp')),'WorkRoot overlaps installation/state roots')
    if a.mode=='Verify':owned_directory(work if work.exists() else work.parent)
    install=home/'.agents/installations'/COMPONENT;unlinked(install)
    receipt_path=install/'installed.json'
    previous=validate_receipt(load(receipt_path),home) if receipt_path.is_file() else None
    if previous:
        if a.python_exe:require(key(a.python_exe)==key(previous['parameters']['PythonExe']),'PythonExe identity change refused')
        require(key(sys.executable)==key(previous['parameters']['PythonExe']),'selected interpreter differs from retained installation')
    if a.mode=='Verify':
        require(previous is not None,'no retained installation to verify')
        if a.harness:require(set(a.harness)==set(previous['parameters']['Harness']),'Verify Harness conflicts with retained selection')
        if a.skills:require(set(a.skills)==set(previous['parameters']['Skills']),'Verify Skills conflicts with retained selection')
        require(a.runtime_config_dir is None or key(a.runtime_config_dir)==key(home/'.agents'/NAMESPACE),'Verify uses retained runtime inputs only')
        return verify(home,install,previous,a.version)
    work=absolute(a.work_root);owned_directory(work)
    source=absolute(a.payload_root);unlinked(source)
    forbidden=[home/'.agents',home/'.claude',home/'.omp']
    original=absolute(a.frontdoor).parent
    if (original/'.git').exists():forbidden.append(original)
    require(not any(under(work,p) or under(p,work) for p in forbidden),'WorkRoot overlaps source/install/state roots')
    require(under(source,work) and under(absolute(a.archive),work),'download and extraction must stay below WorkRoot')
    for env_key in ('TEMP','TMP','TMPDIR','CLAUDE_CODE_TMPDIR'):os.environ[env_key]=str(work)
    for env_key,child in (('BUN_INSTALL_CACHE_DIR','bun'),('npm_config_cache','npm'),('UV_CACHE_DIR','uv'),('PIP_CACHE_DIR','pip'),('XDG_CACHE_HOME','xdg')):
        cache=work/'cache'/child;unlinked(cache)
        os.environ[env_key]=str(cache)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    m=package_validate(source,absolute(a.archive),a.archive_sha256,a.source_commit,a.version,absolute(a.frontdoor))
    declaration=load(source/'deploy/exports.json')
    harness=sorted(set((previous or {}).get('parameters',{}).get('Harness',[]))|set(a.harness or []))
    require(harness and set(harness)<={'claude','codex','omp'},'explicit Harness is required on first installation')
    all_fronts=sorted({r['destination'].split('/')[3] for r in declaration['entries'] if r['destination'].startswith('harness/.agents/skills/') and r['destination'].endswith('/SKILL.md')})
    skills=sorted(set((previous or {}).get('parameters',{}).get('Skills',[]))|set(a.skills or ([] if previous else all_fronts)))
    rows,closure,wiki=selected_entries(declaration,skills,harness)
    # A package version cannot implicitly downgrade installed code.
    if previous:
        require(tuple(map(int,TAG.fullmatch(a.version).groups()))>=tuple(map(int,TAG.fullmatch(previous['version']).groups())),'downgrade refused')
        if a.version==previous['version']:
            require(m['source_commit']==previous['source_commit'] and a.archive_sha256==previous['archive_sha256'],'same-version release identity changed; refusing a retargeted tag or replaced archive')
        verify(home,install,previous,previous['version'])
    def recognized(front):
        path=home/'.agents/skills'/front/'SKILL.md'
        if not path.is_file():return False
        unlinked(path,allow_missing=False)
        text=path.read_text(encoding='utf-8-sig')
        return re.search(r'^name:\s*[\"\']?'+re.escape(front)+r'[\"\']?\s*$',text,re.MULTILINE) is not None
    recognizable=previous is not None or any(recognized(x) for x in all_fronts)
    policy_required=load(install/'configuration.json')['policy_required'] if previous else recognizable
    holds=policy(home,policy_required)
    require(previous is not None or not recognizable or a.adopt_existing,'recognizable existing installation requires -AdoptExisting')
    other='agent-skills' if PRIVATE else 'skills-private'
    other_receipt=home/'.agents/installations'/other/'installed.json'
    foreign=load(other_receipt) if other_receipt.is_file() else None
    foreign_files={key(r['path']) for r in foreign.get('files',[])} if foreign else set()
    if name('discriminator') in closure:
        opposite=home/'.agents/skills'/('discriminator' if PRIVATE else 'ash_discriminator')
        require(not opposite.exists(),'discriminator tool ownership collision between distributions')
    modes={r['path']:r['mode'] for r in m['files']}
    targets={};held=[]
    canonical=home/'.agents/skills'
    unlinked(canonical)
    native=home/'.claude/skills'
    shared_claude=False
    if 'claude' in harness and (native.is_symlink() or native.exists() and getattr(native.lstat(),'st_file_attributes',0)&0x400):
        require(key(native.resolve(strict=True))==key(canonical.resolve(strict=True)),'foreign Claude skills junction refused')
        shared_claude=True
    elif 'claude' in harness:unlinked(native)
    for row in rows:
        dest=row['destination'];relative=dest.removeprefix('harness/');target=home/relative
        content=(source/'payload'/relative).read_bytes();mode=modes['payload/'+relative]
        reason=hold_reason(dest,holds)
        projections=[target]
        if 'claude' in harness and not shared_claude and relative.startswith('.agents/skills/'):
            # Native fronts only; helpers remain at their canonical absolute paths.
            if relative.endswith('/SKILL.md') and relative.split('/')[2] in closure:
                projections.append(native/Path(relative).relative_to('.agents/skills'))
        for target in projections:
            if reason:held.append({'path':str(target),'reason':reason})
            else:targets[target]=(content,mode)
    if PRIVATE and any('/ash_save/' in x['path'].replace('\\','/') or '/ash_restore/' in x['path'].replace('\\','/') for x in held):
        for front in ('ash_save','ash_restore'):
            path=home/'.agents/skills'/front/'SKILL.md'
            if path.is_file() and 'ash_relay_name' in path.read_text(encoding='utf-8-sig'):
                legacy=home/'.agents/skills/ash_relay_name'
                if legacy.exists():
                    held.append({'path':str(legacy),'reason':'EOL-pending: required by held '+front+' caller; no hold release is authorized'})
                    break
    namespace=home/'.agents'/NAMESPACE;unlinked(namespace)
    needs_runtime=wiki or 'omp' in harness and name('discriminator') in closure
    runtime_files={};runtime=None;runtime_source=absolute(a.runtime_config_dir) if a.runtime_config_dir else None
    if runtime_source:
        unlinked(runtime_source)
        require(runtime_source.is_dir() and any((runtime_source/f).is_file() for f in ('runtime.json','omp-install.json')),'RuntimeConfigDir must contain the selected nonsecret runtime inputs')
    for filename in ('runtime.json','omp-install.json') if needs_runtime or runtime_source or previous and 'RuntimeConfigDir' in previous['parameters'] else ():
        target=namespace/filename;unlinked(target)
        supplied=runtime_source/filename if runtime_source else None
        if supplied and supplied.exists():
            unlinked(supplied,allow_missing=False)
            require(not target.exists() or supplied.read_bytes()==target.read_bytes(),'existing local runtime input conflicts; explicit reviewed change required')
        selected=target if target.is_file() else supplied
        required=needs_runtime and (filename=='runtime.json' or 'omp' in harness)
        require(not required or selected is not None and selected.is_file(),f'prerequisite: validated RuntimeConfigDir/{filename}')
        if selected and selected.is_file():
            if filename=='omp-install.json':omp_manifest(selected)
            if filename=='runtime.json':runtime=runtime_validate(selected,home)
            if filename=='runtime.json' and 'omp' in harness and 'review_extension' in runtime.get('workflow_native',{}).get('omp',{}):
                reference=runtime['workflow_native']['omp']['review_extension']
                planned=targets.get(Path(reference['path']))
                require(planned is not None and sha(planned[0])==reference['sha256'],'runtime review-extension hash differs from selected package')
            runtime_files[filename]=sha(selected.read_bytes())
    omp_result=None;contributions={}
    if 'omp' in harness:
        # Validate manifest from retained or supplied directory without writing live inputs.
        manifest_dir=namespace if (namespace/'omp-install.json').is_file() else runtime_source
        exe=omp_executable(runtime,manifest_dir)
        extensions=[p for p in targets if str(p).replace('\\','/').endswith('/extensions/'+name('discriminator')+'/index.ts')]
        omp_result=omp_projection(home,work,exe,extensions)
        contributions=omp_result[2]
    config_spec={'runtime_files':runtime_files,'omp':contributions,'policy_required':PRIVATE and (home/'.agents/workstation-config/install-policy.json').is_file()}
    for filename in ('install.ps1','manifest.json'):
        targets[install/filename]=((source/filename).read_bytes(),modes.get(filename,'100644'))
    targets[install/'install.py']=((source/'deploy/install.py').read_bytes(),modes['deploy/install.py'])
    targets[install/'exports.json']=((source/'deploy/exports.json').read_bytes(),modes['deploy/exports.json'])
    targets[install/'retired.json']=((source/'deploy/retired.json').read_bytes(),modes['deploy/retired.json'])
    for legal in ('LICENSE','NOTICE'):
        if legal in modes:targets[install/legal]=((source/legal).read_bytes(),modes[legal])
    retirement=load(source/'deploy/retired.json')
    require(set(retirement)=={'schema_version','paths'} and retirement['schema_version']==1 and isinstance(retirement['paths'],list),'invalid explicit retirement declaration')
    retired_destinations={rel(x) for x in retirement['paths']}
    require(len(retired_destinations)==len(retirement['paths']) and all(x.startswith('harness/.') for x in retired_destinations),'invalid/duplicate retirement destination')
    targets[install/'configuration.json']=(data_json(config_spec),'100644')
    old={key(r['path']):r for r in previous['files']} if previous else {}
    changes=[];retired=[]
    for path,(content,mode) in targets.items():
        require(allowed(path,home) and key(path) not in foreign_files,'distribution ownership collision: '+str(path));unlinked(path)
        if path.exists():
            require(path.is_file(),'owned target is not a regular file: '+str(path))
            if key(path) in old:require(sha(path.read_bytes())==old[key(path)]['sha256'],'immutable file conflict: '+str(path))
            else:
                require(a.adopt_existing and recognizable,'foreign same-name file requires recognizable -AdoptExisting: '+str(path))
                if path.name=='SKILL.md':
                    existing=path.read_text(encoding='utf-8-sig')
                    require(re.search(r'^name:\s*[\"\']?'+re.escape(path.parent.name)+r'[\"\']?\s*$',existing,re.MULTILINE) is not None,'foreign skill front refuses adoption: '+str(path))
        if not path.exists() or path.read_bytes()!=content:changes.append(path)
    selected_keys={key(p) for p in targets};held_keys={key(p['path']) for p in held}
    for row in previous['files'] if previous else []:
        path=Path(row['path'])
        if key(path) not in selected_keys and key(path) not in held_keys:
            relative='harness/'+path.relative_to(home).as_posix()
            if under(path,home/'.claude/skills'):
                relative='harness/.agents/skills/'+path.relative_to(home/'.claude/skills').as_posix()
            require(relative in retired_destinations,'obsolete owned file requires explicit retirement declaration: '+str(path))
            unlinked(path,allow_missing=False);require(sha(path.read_bytes())==row['sha256'],'edited retired file conflict: '+str(path));retired.append(path)
    # Configuration is surgical and separate from immutable ownership.
    configs={}
    for filename in runtime_files:
        target=namespace/filename
        if not target.is_file():configs[target]=(runtime_source/filename).read_bytes()
    if omp_result and (not omp_result[0].exists() or omp_result[0].read_bytes()!=omp_result[1]):configs[omp_result[0]]=omp_result[1]
    for path in configs:unlinked(path)
    report={'component':COMPONENT,'version':a.version,'mode':a.mode,'write_paths':list(map(str,changes))+list(map(str,configs)),'retire_paths':list(map(str,retired)),'held_paths':held}
    if a.mode=='Check':return report
    if previous and not changes and not configs and not retired and previous['parameters']['Harness']==harness and previous['parameters']['Skills']==skills and previous['version']==a.version and previous['held_paths']==held:
        return {**report,'status':'verified-no-op'}
    # Complete preflight above; preservation below is never automatically purged.
    affected=[*changes,*configs,*retired,receipt_path]
    preimages={p:sha(p.read_bytes()) if p.is_file() else None for p in affected}
    def unchanged(path):
        unlinked(path)
        require((sha(path.read_bytes()) if path.is_file() else None)==preimages[path],'target changed after preflight: '+str(path))
    backup=work/'backups'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex)
    unlinked(backup);backup.mkdir(parents=True);private_directory(backup)
    backup_rows=[]
    for index,path in enumerate(affected):
        if path.is_file():
            content=path.read_bytes();saved=backup/str(index);saved.write_bytes(content)
            backup_rows.append({'path':str(path),'backup':str(saved),'sha256':sha(content)})
    (backup/'preservation.json').write_bytes(data_json({'component':COMPONENT,'files':backup_rows}))
    report['backup_root']=str(backup)
    effected=[]
    try:
        for path in affected:unchanged(path)
        for path in changes:
            unchanged(path);atomic(path,*targets[path]);effected.append(str(path))
        for path,content in configs.items():
            unchanged(path);atomic(path,content);effected.append(str(path))
        for path in retired:unchanged(path);path.unlink();effected.append(str(path))
        executable_change=any(p.suffix.lower() in ('.py','.ts','.js','.ps1','.sh','.bat','.exe') for p in [*changes,*retired])
        stamp=datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if executable_change else (previous['last_code_change_utc'] if previous else None)
        receipt={'schema_version':1,'component':COMPONENT,'repository':REPOSITORY,'version':a.version,'source_commit':m['source_commit'],'archive_sha256':a.archive_sha256,'parameters':{'Harness':harness,'Skills':skills,'PythonExe':str(absolute(sys.executable)),'UserRoot':str(home)},'files':[{'path':str(p),'sha256':sha(content),'mode':mode} for p,(content,mode) in sorted(targets.items(),key=lambda x:key(x[0]))],'configuration_paths':[str(namespace/f) for f in runtime_files]+([str(omp_result[0])] if omp_result else []),'held_paths':held,'last_code_change_utc':stamp}
        if runtime_files:receipt['parameters']['RuntimeConfigDir']=str(namespace)
        # Verify all applied owned bytes/configuration before publishing success metadata.
        for row in receipt['files']:require(sha(Path(row['path']).read_bytes())==row['sha256'],'post-write file mismatch')
        configuration_verify(home,install,receipt)
        retained_projection(home,install,receipt,m)
        unchanged(receipt_path)
        atomic(receipt_path,data_json(receipt))
    except Exception:
        failure={**report,'status':'partial-failure','possible_effects':effected,'success_receipt_written':False}
        (backup/'failure.json').write_bytes(data_json(failure));print(json.dumps(failure),file=sys.stderr)
        raise
    return {**report,'status':'installed','activation':'not-applicable'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('Check','Install','Verify'),default='Install')
    p.add_argument('--version',required=True);p.add_argument('--work-root',required=True);p.add_argument('--user-root')
    p.add_argument('--python-exe');p.add_argument('--runtime-config-dir');p.add_argument('--harness',action='append');p.add_argument('--skills',action='append')
    p.add_argument('--adopt-existing',action='store_true')
    for option in ('payload-root','archive','archive-sha256','source-commit','frontdoor'):p.add_argument('--'+option)
    a=p.parse_args()
    try:
        result=execute(a);print(json.dumps(result,ensure_ascii=False))
    except (Refusal,OSError,KeyError,TypeError,json.JSONDecodeError,subprocess.SubprocessError) as exc:
        p.exit(1,f'Installer refused: {exc}\n')

if __name__=='__main__':main()
