"""Behavioral package/install fixtures; the actual account observer is intercepted explicitly."""
from __future__ import annotations
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def module(path,label):
    spec=importlib.util.spec_from_file_location(label,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

installer=module(ROOT/'deploy/install.py','skills_install_fixture')
packager=module(ROOT/'deploy/package_release.py','skills_package_fixture')

class LocalInstallation(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='skills-local-install-')
        self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name).resolve()
        self.home=self.base/'profile';self.home.mkdir()
        self.work=self.base/'work';self.work.mkdir()
        self.source=self.base/'source';self.source.mkdir()
        self.install=self.home/'.agents/installations'/installer.COMPONENT
        self.observer=mock.patch.object(installer,'actual_profile',return_value=self.home)
        self.observer.start();self.addCleanup(self.observer.stop)
        # Fixture account interception is not a claim about a real target account.
        self.environment=mock.patch.dict(os.environ,{k:str(self.work) for k in ('TEMP','TMP','TMPDIR','CLAUDE_CODE_TMPDIR')})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.declaration=json.loads((ROOT/'deploy/exports.json').read_text(encoding='utf-8'))
        for row in self.declaration['entries']:
            src=ROOT/row['source'];dst=self.source/row['source']
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        for relative in ('install.ps1','deploy/install.py','deploy/exports.json','deploy/retired.json'):
            dst=self.source/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,dst)
        for relative in ('LICENSE','NOTICE'):
            if (ROOT/relative).is_file():shutil.copyfile(ROOT/relative,self.source/relative)
        # A finite synthetic resource exercises retirement without deleting one
        # of the required seven save helpers from a supposedly complete release.
        resource=self.source/'fixture-resources/retired.txt';resource.parent.mkdir()
        resource.write_bytes(b'synthetic prior owned resource')
        self.declaration['entries'].append({'source':'fixture-resources/retired.txt','destination':'harness/.agents/skills/'+installer.name('save')+'/retired-resource.txt'})
        (self.source/'deploy/exports.json').write_text(json.dumps(self.declaration),encoding='utf-8')
        self.git('init','-b','main')
        self.git('config','user.name','Synthetic fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.commit()
        if installer.PRIVATE:self.write_policy([])
        self.build('v1.0.0')

    def git(self,*args):
        env=dict(os.environ,GIT_OPTIONAL_LOCKS='0')
        return subprocess.run(['git','-c','core.fsmonitor=false','-c','diff.autoRefreshIndex=false','-C',str(self.source),*args],check=True,capture_output=True,env=env).stdout.decode().strip()

    def commit(self):
        self.git('add','--all');self.git('commit','-m','Synthetic installer fixture')
        self.revision=self.git('rev-parse','HEAD')

    def write_policy(self,rows):
        path=self.home/'.agents/workstation-config/install-policy.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'schema_version':1,'machine':socket.gethostname(),'userprofile':str(self.home),'deploy_exclude':rows}),encoding='utf-8')

    def build(self,version):
        self.version=version;self.output=self.work/('release-'+version)
        with contextlib.redirect_stdout(io.StringIO()):packager.build(self.source,self.revision,version,self.output)
        self.archive=self.output/f'{installer.COMPONENT}-{version}.zip'
        self.digest=installer.sha(self.archive.read_bytes())
        self.payload=self.output/'extracted';self.payload.mkdir()
        with zipfile.ZipFile(self.archive) as z:z.extractall(self.payload)
        self.frontdoor=self.output/'install.ps1'

    def args(self,**changes):
        value=dict(mode='Install',version=self.version,work_root=str(self.work),user_root=str(self.home),python_exe=str(Path(sys.executable).absolute()),runtime_config_dir=None,harness=['codex'],skills=[installer.name('save')],adopt_existing=False,payload_root=str(self.payload),archive=str(self.archive),archive_sha256=self.digest,source_commit=self.revision,frontdoor=str(self.frontdoor))
        value.update(changes);return argparse.Namespace(**value)

    def run_install(self,**changes):return installer.execute(self.args(**changes))

    def receipt(self):return installer.load(self.install/'installed.json')

    def snapshot(self):
        return {p.relative_to(self.home).as_posix():p.read_bytes() for p in self.home.rglob('*') if p.is_file()}

    @unittest.skipUnless(os.name=='nt','Windows ownership uses the native PowerShell host')
    def test_inherited_foreign_security_module_cannot_replace_owner_checks(self):
        modules=self.base/'foreign-modules'
        module_root=modules/'Microsoft.PowerShell.Security';module_root.mkdir(parents=True)
        marker=self.work/'foreign-module-loaded.txt'
        (module_root/'Microsoft.PowerShell.Security.psd1').write_text("@{RootModule='foreign.psm1';ModuleVersion='99.0.0';FunctionsToExport=@('Get-Acl','Set-Acl')}\n",encoding='utf-8')
        escaped=str(marker).replace("'","''")
        (module_root/'foreign.psm1').write_text("[IO.File]::WriteAllText('"+escaped+"','foreign code loaded');function Get-Acl {throw 'foreign owner observer'};function Set-Acl {throw 'foreign ACL writer'};Export-ModuleMember -Function Get-Acl,Set-Acl\n",encoding='utf-8')
        with mock.patch.dict(os.environ,{'PSModulePath':str(modules)}):
            self.run_install()
            before=self.snapshot()
            result=self.run_install(mode='Verify',harness=None,skills=None,payload_root=None,archive=None,archive_sha256=None,source_commit=None,frontdoor=None)
        self.assertEqual(result['status'],'verified')
        self.assertEqual(before,self.snapshot())
        self.assertFalse(marker.exists(),'An inherited foreign security module executed')

    def remove_source_fixture(self):
        self.assertEqual(self.source,self.base/'source')
        self.assertFalse(self.source.is_symlink())
        def clear_owned_git_readonly(function,path,error):
            candidate=Path(path).absolute()
            metadata=candidate.lstat()
            if not (os.name=='nt' and isinstance(error[1],PermissionError)
                    and candidate.is_relative_to(self.source/'.git')
                    and stat.S_ISREG(metadata.st_mode) and not candidate.is_symlink()
                    and metadata.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY):
                raise error[1]
            candidate.chmod(metadata.st_mode | stat.S_IWRITE)
            function(path)
        shutil.rmtree(self.source,onerror=clear_owned_git_readonly)

    def test_check_then_fresh_selected_install_and_offline_verify(self):
        before=self.snapshot()
        report=self.run_install(mode='Check')
        self.assertTrue(report['write_paths']);self.assertEqual(before,self.snapshot())
        report=self.run_install();self.assertEqual(report['status'],'installed')
        save=self.home/'.agents/skills'/installer.name('save')
        self.assertTrue((save/'SKILL.md').is_file())
        self.assertEqual(len(list((save/'scripts').glob('*.py'))),7)
        self.assertFalse((self.home/'.claude').exists());self.assertFalse((self.home/'.omp').exists())
        self.assertFalse((self.home/'.agents/skills'/installer.name('wiki')).exists())
        self.assertEqual(set(self.receipt()),installer.RECEIPT_FIELDS)
        neighbor=self.home/'.agents/skills/operator-notes.txt'
        neighbor.write_bytes(b'synthetic unmanaged user data')
        shutil.rmtree(self.output);self.remove_source_fixture()
        self.assertFalse(self.output.exists());self.assertFalse(self.source.exists())
        before=self.snapshot()
        result=self.run_install(mode='Verify',harness=None,skills=None,payload_root=None,archive=None,archive_sha256=None,source_commit=None,frontdoor=None)
        self.assertEqual(result['status'],'verified');self.assertEqual(before,self.snapshot())
        self.assertEqual(neighbor.read_bytes(),b'synthetic unmanaged user data')

    def test_missing_first_harness_or_unknown_skill_does_not_write(self):
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'Harness'):self.run_install(harness=None)
        with self.assertRaisesRegex(installer.Refusal,'unknown Skills'):self.run_install(skills=['unknown-skill'])
        self.assertEqual(before,self.snapshot())

    def test_save_ignores_absent_wiki_runtime_inputs(self):
        with mock.patch.object(installer,'runtime_validate',side_effect=AssertionError('unselected runtime must not be inspected')):
            result=self.run_install()
        self.assertEqual(result['status'],'installed')
        self.assertEqual(self.receipt()['configuration_paths'],[])

    def test_omitted_selection_retains_union_and_claude_native_fronts(self):
        self.run_install()
        self.run_install(harness=['claude'],skills=[installer.name('obsidian')])
        r=self.receipt()
        self.assertEqual(r['parameters']['Harness'],['claude','codex'])
        self.assertEqual(set(r['parameters']['Skills']),{installer.name('save'),installer.name('obsidian')})
        self.assertTrue((self.home/'.claude/skills'/installer.name('save')/'SKILL.md').is_file())
        self.assertFalse((self.home/'.claude/skills'/installer.name('save')/'scripts').exists())
        self.run_install(harness=None,skills=None)
        self.assertEqual(r,self.receipt())

    def test_same_version_verifies_and_detects_immutable_drift(self):
        self.run_install();r=self.receipt();before=self.snapshot()
        result=self.run_install(harness=None,skills=None)
        self.assertEqual(result['status'],'verified-no-op');self.assertEqual(before,self.snapshot())
        path=self.home/'.agents/skills'/installer.name('save')/'scripts/citemap.py'
        path.write_bytes(path.read_bytes()+b'\n# local edit\n')
        with self.assertRaisesRegex(installer.Refusal,'drift'):self.run_install()
        with self.assertRaisesRegex(installer.Refusal,'drift'):self.run_install(mode='Verify',harness=None,skills=None)
        self.assertEqual(r,self.receipt())

    def test_explicit_retirement_preserves_unknown_state_and_neighbors(self):
        self.run_install()
        for relative in ('.agents/skills/neighbor.txt','.agents/services/broker/data.sqlite-wal','.agents/services/copilot-proxy/private-token'):
            path=self.home/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'synthetic-private-state')
        removed=next(r for r in self.declaration['entries'] if r['destination'].endswith('/retired-resource.txt'))
        self.declaration['entries'].remove(removed)
        (self.source/'deploy/exports.json').write_text(json.dumps(self.declaration),encoding='utf-8')
        (self.source/'deploy/retired.json').write_text(json.dumps({'schema_version':1,'paths':[removed['destination']]}),encoding='utf-8')
        self.commit();self.build('v1.0.1')
        self.run_install(harness=None,skills=None)
        self.assertFalse((self.home/removed['destination'].removeprefix('harness/')).exists())
        for relative in ('.agents/skills/neighbor.txt','.agents/services/broker/data.sqlite-wal','.agents/services/copilot-proxy/private-token'):
            self.assertEqual((self.home/relative).read_bytes(),b'synthetic-private-state')
        self.assertEqual(self.run_install(mode='Verify',harness=None,skills=None)['status'],'verified')

    def test_removed_export_without_explicit_retirement_refuses_before_writes(self):
        self.run_install();before=self.snapshot()
        self.declaration['entries']=[r for r in self.declaration['entries'] if not r['destination'].endswith('/retired-resource.txt')]
        (self.source/'deploy/exports.json').write_text(json.dumps(self.declaration),encoding='utf-8');self.commit();self.build('v1.0.1')
        with self.assertRaisesRegex(installer.Refusal,'explicit retirement'):self.run_install(harness=None,skills=None)
        self.assertEqual(before,self.snapshot())

    def test_adoption_is_explicit_and_every_overwritten_file_is_preserved(self):
        front=self.home/'.agents/skills'/installer.name('save')/'SKILL.md'
        front.parent.mkdir(parents=True);front.write_text('---\nname: '+installer.name('save')+'\n---\nSynthetic old front\n',encoding='utf-8')
        original=front.read_bytes();before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'AdoptExisting'):self.run_install()
        self.assertEqual(before,self.snapshot())
        result=self.run_install(adopt_existing=True)
        preservation=installer.load(Path(result['backup_root'])/'preservation.json')
        row=next(x for x in preservation['files'] if installer.key(x['path'])==installer.key(front))
        self.assertEqual(Path(row['backup']).read_bytes(),original)

    def test_archive_wrong_binding_and_unsafe_members_refuse_without_live_write(self):
        before=self.snapshot()
        for arguments in ({'archive_sha256':'0'*64},{'source_commit':'0'*40},{'version':'v1.0.1'}):
            with self.assertRaises(installer.Refusal):self.run_install(**arguments)
        original=self.archive.read_bytes()
        for member in ('../escape.py','/absolute.py','INSTALL.PS1','extra.py'):
            self.archive.write_bytes(original)
            with zipfile.ZipFile(self.archive,'a') as z:z.writestr(member,b'not executed')
            with self.assertRaises(installer.Refusal):self.run_install(archive_sha256=installer.sha(self.archive.read_bytes()))
        self.assertEqual(before,self.snapshot())

    def test_wrong_profile_and_distribution_collision_refuse(self):
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'account'):self.run_install(user_root=str(self.work))
        other='agent-skills' if installer.PRIVATE else 'skills-private'
        path=self.home/'.agents/installations'/other/'installed.json';path.parent.mkdir(parents=True)
        target=self.home/'.agents/skills'/installer.name('save')/'SKILL.md'
        path.write_text(json.dumps({'files':[{'path':str(target)}]}),encoding='utf-8')
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'collision'):self.run_install()
        self.assertEqual(before,self.snapshot())

    def test_interrupted_write_retains_diagnostics_without_success_receipt(self):
        real=installer.atomic;count=0
        def interrupted(path,*args):
            nonlocal count
            count+=1
            if count==2:raise OSError('synthetic interrupted copy')
            return real(path,*args)
        with mock.patch.object(installer,'atomic',side_effect=interrupted),contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(OSError,'interrupted'):self.run_install()
        self.assertFalse((self.install/'installed.json').exists())
        reports=list((self.work/'backups').glob('*/failure.json'))
        self.assertEqual(len(reports),1)
        failure=installer.load(reports[0]);self.assertEqual(failure['status'],'partial-failure')
        self.assertEqual(len(failure['possible_effects']),1);self.assertFalse(failure['success_receipt_written'])

    def test_complete_discriminator_closure_is_not_harness_filtered(self):
        expected={'SKILL.md','agents/'+installer.name('discriminator')+'.toml',
                  'assets/preamble_envelope.txt','assets/preamble_finding.txt','assets/rubric.lock.json','assets/rubric.txt',
                  'claude_reviewer.py','envelope.py','envelope_schema.py','family_attestation.py','native_review.py',
                  'packet.py','request.py','result.py','supervisor.py','telemetry.py','transport.py',
                  *('contracts/'+contract+'.md' for contract in ('code-fleet','code-service','configuration','model-instruction',
                    'pipeline-template','plan-design','project-state','research-report','vault-raw','vault-wiki'))}
        prefix='harness/.agents/skills/'+installer.name('discriminator')+'/'
        for harness in (['codex'],['claude'],['omp']):
            rows,closure,wiki=installer.selected_entries(self.declaration,[installer.name('discriminator')],harness)
            runtime=[r['destination'].removeprefix(prefix) for r in rows if r['destination'].startswith(prefix)]
            self.assertEqual(len(runtime),27)
            self.assertEqual(set(runtime),expected)
        rows,closure,_=installer.selected_entries(self.declaration,[installer.name('restore')],['codex'])
        helpers=[r for r in rows if '/'+installer.name('save')+'/scripts/' in r['destination']]
        self.assertEqual(len(helpers),7)

    def test_held_save_front_is_not_claimed_and_legacy_helper_remains_protected(self):
        if not installer.PRIVATE:self.skipTest('private-only hold policy')
        front=self.home/'.agents/skills/ash_save/SKILL.md';front.parent.mkdir(parents=True)
        front.write_text('---\nname: ash_save\n---\nuses ash_relay_name\n',encoding='utf-8')
        legacy=self.home/'.agents/skills/ash_relay_name/scripts/set_name.py';legacy.parent.mkdir(parents=True);legacy.write_bytes(b'old protected helper')
        rows=[{'path':'harness/.agents/skills/ash_save/**','reason':'synthetic hold','expires':'explicit reviewed release only','added':'2026-01-01'}]
        self.write_policy(rows)
        result=self.run_install(adopt_existing=True)
        r=self.receipt()
        self.assertIn(str(front),{x['path'] for x in r['held_paths']})
        self.assertNotIn(str(front),{x['path'] for x in r['files']})
        self.assertIn(str(legacy.parent.parent),{x['path'] for x in r['held_paths']})
        self.assertEqual(legacy.read_bytes(),b'old protected helper')
        self.assertIn('ash_relay_name',front.read_text(encoding='utf-8'))
        self.assertEqual(installer.load(self.home/'.agents/workstation-config/install-policy.json')['deploy_exclude'],rows)
        self.assertEqual(self.run_install(mode='Verify',harness=None,skills=None)['status'],'verified')

    def test_literal_committed_tree_not_dirty_worktree(self):
        source=self.source/next(r['source'] for r in self.declaration['entries'] if r['destination'].endswith('/'+installer.name('save')+'/SKILL.md'))
        original=source.read_bytes();source.write_bytes(b'dirty unaccepted bytes')
        out=self.work/'second-build'
        with contextlib.redirect_stdout(io.StringIO()):packager.build(self.source,self.revision,self.version,out)
        with zipfile.ZipFile(out/f'{installer.COMPONENT}-{self.version}.zip') as z:
            self.assertEqual(z.read('payload/.agents/skills/'+installer.name('save')+'/SKILL.md'),original)
        with self.assertRaisesRegex(ValueError,'literal'):packager.build(self.source,'HEAD',self.version,self.work/'bad-revision')

    def test_explicit_local_runtime_inputs_are_retained_without_source_directory(self):
        inputs=self.work/'input-config';inputs.mkdir()
        value={'machine':socket.gethostname(),'tokens':{'__USERPROFILE__':str(self.home),'__OMP_PYTHON__':str(Path(sys.executable).absolute())},'omp_managed':{'enabled':False},'workflow_native':{}}
        (inputs/'runtime.json').write_text(json.dumps(value),encoding='utf-8')
        self.run_install(runtime_config_dir=str(inputs))
        retained=self.home/'.agents'/installer.NAMESPACE/'runtime.json'
        self.assertEqual(installer.load(retained),value)
        shutil.rmtree(inputs)
        self.run_install(harness=None,skills=None)
        self.assertIn(str(retained),self.receipt()['configuration_paths'])
        before=self.snapshot()
        self.assertEqual(self.run_install(mode='Verify',harness=None,skills=None)['status'],'verified')
        self.assertEqual(before,self.snapshot())

    def test_removed_certification_fields_and_changed_runtime_inputs_refuse(self):
        inputs=self.work/'input-config';inputs.mkdir()
        value={'machine':socket.gethostname(),'tokens':{'__USERPROFILE__':str(self.home),'__OMP_PYTHON__':str(Path(sys.executable).absolute())},'workflow_native':{'omp':{'capability_receipt_path':None}}}
        path=inputs/'runtime.json';path.write_text(json.dumps(value),encoding='utf-8')
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'certification'):self.run_install(runtime_config_dir=str(inputs))
        self.assertEqual(before,self.snapshot())
        value['workflow_native']={};path.write_text(json.dumps(value),encoding='utf-8')
        self.run_install(runtime_config_dir=str(inputs))
        value['synthetic_note']='changed';path.write_text(json.dumps(value),encoding='utf-8')
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'conflicts'):self.run_install(runtime_config_dir=str(inputs))
        self.assertEqual(before,self.snapshot())

    def test_pristine_private_install_without_legacy_policy_retains_fresh_proof(self):
        if not installer.PRIVATE:self.skipTest('private-only hold policy')
        (self.home/'.agents/workstation-config/install-policy.json').unlink()
        self.run_install()
        self.assertFalse(installer.load(self.install/'configuration.json')['policy_required'])
        self.assertEqual(self.run_install(mode='Verify',harness=None,skills=None)['status'],'verified')
        self.assertEqual(self.run_install(harness=None,skills=None)['status'],'verified-no-op')

    def test_missing_private_policy_refuses_without_live_writes(self):
        if not installer.PRIVATE:self.skipTest('private-only hold policy')
        (self.home/'.agents/workstation-config/install-policy.json').unlink()
        front=self.home/'.agents/skills'/installer.name('save')/'SKILL.md';front.parent.mkdir(parents=True)
        front.write_text('---\nname: '+installer.name('save')+'\n---\nSynthetic legacy front\n',encoding='utf-8')
        before=self.snapshot()
        with self.assertRaisesRegex(installer.Refusal,'install-policy'):self.run_install()
        self.assertEqual(before,self.snapshot())

    def test_preserves_canonical_claude_skills_junction(self):
        self.run_install()
        native=self.home/'.claude/skills';native.parent.mkdir()
        canonical=self.home/'.agents/skills'
        if os.name=='nt':
            result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(native),str(canonical)],capture_output=True)
            if result.returncode:self.skipTest('fixture host cannot create its owned directory junction')
        else:native.symlink_to(canonical,target_is_directory=True)
        target=native.resolve(strict=True)
        self.run_install(harness=['claude'],skills=None)
        self.assertEqual(native.resolve(strict=True),target)
        self.assertTrue(native.is_symlink() or getattr(native.lstat(),'st_file_attributes',0)&0x400)
        self.assertEqual(self.run_install(mode='Verify',harness=None,skills=None)['status'],'verified')

    def test_selected_wiki_and_omp_web_have_finite_complete_dependencies(self):
        rows,closure,wiki=installer.selected_entries(self.declaration,[installer.name('wiki-compile')],['codex'])
        self.assertTrue(wiki)
        self.assertTrue({installer.name('wiki'),installer.name('obsidian'),installer.name('discriminator')}<=set(closure))
        self.assertTrue(any('/_workflow/' in row['destination'] for row in rows))
        rows,closure,_=installer.selected_entries(self.declaration,[installer.name('websearch')],['omp'])
        self.assertTrue({installer.name('webfetch'),installer.name('websearch')}<=set(closure))
        self.assertEqual(sum(row['destination'].startswith('harness/.omp/agent/skills/') for row in rows),2)
        self.assertFalse(any('/profiles/' in row['destination'] for row in rows))

    def omp_runtime_fixture(self):
        root=self.work/'existing-omp-runtime'
        package=root/'packages/pi-coding-agent';(package/'dist').mkdir(parents=True)
        (root/'bin').mkdir()
        (root/'bin/omp.exe').write_bytes(b'synthetic existing launcher; never executed')
        (root/'bin/bun.exe').write_bytes(b'synthetic existing interpreter; never executed')
        (package/'dist/cli.js').write_bytes(b'export const syntheticPackageInput = true;\n')
        (package/'package.json').write_text(json.dumps({'name':'@oh-my-pi/pi-coding-agent'}),encoding='utf-8')
        namespace=self.work/'omp-install-input';namespace.mkdir()
        manifest={'schema':1,'package':'@oh-my-pi/pi-coding-agent','package_tree_relative':'packages/pi-coding-agent',
                  'executable_relative':'bin/omp.exe','payload_relative':'packages/pi-coding-agent/dist/cli.js',
                  'installer':{'runtime_relative':'bin/bun.exe'}}
        (namespace/'omp-install.json').write_text(json.dumps(manifest),encoding='utf-8')
        return root,package,namespace,{'omp_managed':{'enabled':True,'install_root':str(root)}}

    def test_existing_readonly_hardlinked_omp_package_inputs_are_not_modified(self):
        root,package,namespace,runtime=self.omp_runtime_fixture()
        cache=self.work/'existing-package-cache';cache.mkdir()
        inputs=[package/'dist/cli.js',package/'package.json']
        aliases=[]
        for path in inputs:
            alias=cache/path.name;os.link(path,alias);aliases.append(alias)
            previous_mode=path.stat().st_mode
            self.addCleanup(os.chmod,path,previous_mode)
            path.chmod(stat.S_IREAD)
            self.assertEqual(path.stat().st_nlink,2)
            self.assertEqual(path.stat().st_ino,alias.stat().st_ino)
            self.assertFalse(path.stat().st_mode & stat.S_IWRITE)
        def observed():
            return [(p.read_bytes(),p.stat().st_mode,p.stat().st_nlink,p.stat().st_ino) for p in [*inputs,*aliases]]
        before=observed()
        self.assertEqual(installer.omp_executable(runtime,namespace),root/'bin/omp.exe')
        self.assertEqual(observed(),before)
        with self.assertRaisesRegex(installer.Refusal,'escapes'):
            installer.omp_package_input(aliases[0],package)
        self.assertEqual(observed(),before)

    def test_omp_package_identity_leaf_must_be_regular_before_read(self):
        root,package,namespace,runtime=self.omp_runtime_fixture()
        identity=package/'package.json';identity.unlink();identity.mkdir()
        with mock.patch.object(installer,'load',wraps=installer.load) as reads:
            with self.assertRaisesRegex(installer.Refusal,'regular contained file'):
                installer.omp_executable(runtime,namespace)
        self.assertNotIn(mock.call(identity),reads.call_args_list)

    def test_owned_installed_hardlink_remains_a_conflict(self):
        self.run_install()
        owned=self.home/'.agents/skills'/installer.name('save')/'scripts/citemap.py'
        alias=self.work/'external-owned-file-alias.py';os.link(owned,alias)
        before=self.snapshot();alias_before=alias.read_bytes()
        self.assertEqual(owned.stat().st_nlink,2)
        for arguments in ({'mode':'Check'},{'mode':'Install'},{'mode':'Verify','harness':None,'skills':None}):
            with self.assertRaisesRegex(installer.Refusal,'hardlinked target'):
                self.run_install(**arguments)
        self.assertEqual(before,self.snapshot());self.assertEqual(alias.read_bytes(),alias_before)
        self.assertEqual(owned.stat().st_ino,alias.stat().st_ino)

    def test_real_omp_public_configuration_projection_when_explicitly_selected(self):
        command=os.environ.get('SKILLS_TEST_OMP_COMMAND')
        if not command:self.skipTest('set SKILLS_TEST_OMP_COMMAND to explicitly select an existing OMP for isolated public-config API verification')
        try:import yaml
        except ImportError:self.skipTest('selected existing Python needs PyYAML for OMP configuration fixtures')
        exe=Path(command).resolve(strict=True)
        target=self.home/'.omp/agent/config.yml';target.parent.mkdir(parents=True)
        target.write_text('syntheticNeighbor: keep\nskills:\n  customDirectories:\n    - synthetic-existing-directory\nextensions: []\n',encoding='utf-8')
        original=target.read_bytes()
        path,content,contribution=installer.omp_projection(self.home,self.work,exe,[])
        self.assertEqual(path,target);self.assertEqual(target.read_bytes(),original)
        result=yaml.safe_load(content)
        self.assertEqual(result['syntheticNeighbor'],'keep')
        self.assertIn('synthetic-existing-directory',result['skills']['customDirectories'])
        self.assertIn(str(self.home/'.agents/skills'),result['skills']['customDirectories'])
        installer.verify_omp_document(result,contribution)

if __name__=='__main__':unittest.main()
