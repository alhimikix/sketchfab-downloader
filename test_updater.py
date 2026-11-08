import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock,patch
import urllib.request
import zipfile

from updater import ReleaseClient, Release, UpdateError, SafeRedirect, extract_package, checked_zip_members
from updater_helper import PROTOCOL, STAGE_PREFIX, install, write_json


class FakeOpener:
    def __init__(self,body):self.body=body;self.requests=[]
    def open(self,request,timeout):self.requests.append(request);return io.BytesIO(self.body)


def metadata(body=b'new application',version='1.4.0'):
    return {'tag_name':'v'+version,'draft':False,'prerelease':False,'assets':[
        {'id':123,'name':f'SketchfabLocal-{version}-windows-x64.exe','state':'uploaded',
         'size':len(body),'digest':'sha256:'+hashlib.sha256(body).hexdigest(),
         'url':'https://untrusted.invalid/ignored'}]}


class ReleaseTests(unittest.TestCase):
    def test_https_keeps_certificate_and_hostname_verification_enabled(self):
        import ssl
        from https_support import ssl_context
        context=ssl_context()
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()['x509_ca'],0)

    def test_public_release_selects_exact_asset_without_credentials_and_does_not_downgrade(self):
        opener=FakeOpener(json.dumps(metadata()).encode());client=ReleaseClient(opener=opener)
        self.assertEqual(client.latest('1.3.0','windows-x64').version,'1.4.0')
        self.assertIsNone(opener.requests[0].get_header('Authorization'))
        self.assertIsNone(client.latest('1.4.0','windows-x64'))
        self.assertIsNone(client.latest('2.0.0','windows-x64'))

    def test_incomplete_release_missing_digest_is_rejected(self):
        data=metadata();del data['assets'][0]['digest']
        with self.assertRaises(UpdateError):ReleaseClient(opener=FakeOpener(json.dumps(data).encode())).latest('1.0.0','windows-x64')

    def test_download_validates_size_and_digest_and_uses_api_asset_id(self):
        body=b'new application';release=Release('1.4.0','v1.4.0','windows-x64',123,'package.exe',len(body),hashlib.sha256(body).hexdigest())
        opener=FakeOpener(body)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/release.name
            ReleaseClient(opener=opener).download(release,path)
            self.assertEqual(path.read_bytes(),body)
            self.assertTrue(opener.requests[0].full_url.endswith('/releases/assets/123'))
            self.assertEqual(opener.requests[0].get_header('Accept'),'application/octet-stream')
            self.assertIsNone(opener.requests[0].get_header('Authorization'))

    def test_corrupted_and_truncated_downloads_leave_no_payload(self):
        release=Release('1.4.0','v1.4.0','windows-x64',123,'package.exe',5,hashlib.sha256(b'valid').hexdigest())
        for body in (b'bad!!',b'bad',b'too long'):
            with self.subTest(body=body),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/release.name
                with self.assertRaises(UpdateError):ReleaseClient(opener=FakeOpener(body)).download(release,path)
                self.assertFalse(path.exists());self.assertFalse(path.with_name(path.name+'.part').exists())

    def test_redirect_never_sends_credentials_to_cdn_and_rejects_other_hosts(self):
        request=urllib.request.Request('https://api.github.com/repos/test/releases/assets/1',headers={'Authorization':'Bearer test-secret','Cookie':'session-cookie'})
        redirect=SafeRedirect().redirect_request(request,None,302,'Found',{},'https://release-assets.githubusercontent.com/payload?signature=example')
        self.assertIsNone(redirect.get_header('Authorization'));self.assertIsNone(redirect.get_header('Cookie'))
        for url in ('http://release-assets.githubusercontent.com/payload','https://untrusted.invalid/payload','https://user:pass@github.com/payload'):
            with self.assertRaises(UpdateError):SafeRedirect().redirect_request(request,None,302,'Found',{},url)

    def test_malicious_tar_and_zip_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'bad.tar.gz'
            with tarfile.open(package,'w:gz') as archive:
                entry=tarfile.TarInfo('SketchfabLocal/../../outside');entry.size=3
                archive.addfile(entry,io.BytesIO(b'bad'))
            with self.assertRaises(UpdateError):extract_package(package,root/'output','linux')
            self.assertFalse((root/'outside').exists())
            for name,link in [('SketchfabLocal.app/../../outside',None),('SketchfabLocal.app/link','../../outside')]:
                raw=io.BytesIO()
                with zipfile.ZipFile(raw,'w') as archive:
                    item=zipfile.ZipInfo(name)
                    if link:item.external_attr=(0o120777 << 16)
                    archive.writestr(item,link or 'bad')
                with zipfile.ZipFile(raw) as archive:
                    with self.assertRaises(UpdateError):checked_zip_members(archive)


class AnonymousAccessTests(unittest.TestCase):
    def test_environment_credentials_are_not_read_or_forwarded(self):
        opener=FakeOpener(json.dumps(metadata()).encode())
        with patch.dict(os.environ,{'GH_TOKEN':'unused-test-token','GITHUB_TOKEN':'unused-other-token',
                                    'SKETCHFAB_LOCAL_GITHUB_TOKEN':'unused-legacy-token'}):
            release=ReleaseClient(opener=opener).latest('1.3.0','windows-x64')
        self.assertEqual(release.version,'1.4.0')
        self.assertIsNone(opener.requests[0].get_header('Authorization'))


class InstallationTests(unittest.TestCase):
    def fixture(self,folder):
        root=Path(folder);stage=root/(STAGE_PREFIX+'test');stage.mkdir()
        target=root/'Application';target.mkdir();(target/'SketchfabLocal').write_text('old')
        (target/'user-model.glb').write_text('user model')
        prepared=stage/'payload';prepared.mkdir();(prepared/'SketchfabLocal').write_text('new')
        manifest=stage/'manifest.json';result=root/'result.json'
        write_json(manifest,{'protocol':PROTOCOL,'kind':'linux','target':str(target),'prepared':str(prepared),
                             'parent_pid':0,'version':'1.4.0','result':str(result)})
        return target,prepared,manifest,result

    def test_success_preserves_user_data_and_keeps_recoverable_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            target,prepared,manifest,result=self.fixture(folder)
            self.assertTrue(install(manifest,launcher=Mock(),receipt_waiter=Mock()))
            self.assertEqual((target/'SketchfabLocal').read_text(),'new')
            self.assertEqual((target/'user-model.glb').read_text(),'user model')
            backup=next(target.parent.glob('Application.previous-*'))
            self.assertEqual((backup/'SketchfabLocal').read_text(),'old')
            self.assertEqual(json.loads(result.read_text())['status'],'success')

    def test_startup_failure_restores_previous_application_and_user_files(self):
        with tempfile.TemporaryDirectory() as folder:
            target,prepared,manifest,result=self.fixture(folder);launcher=Mock()
            self.assertFalse(install(manifest,launcher=launcher,receipt_waiter=Mock(side_effect=TimeoutError())))
            self.assertEqual((target/'SketchfabLocal').read_text(),'old')
            self.assertEqual((target/'user-model.glb').read_text(),'user model')
            self.assertEqual(json.loads(result.read_text())['key'],'update.start_failed')
            self.assertEqual(launcher.call_count,2)

    def test_interrupted_replacement_restores_old_app(self):
        with tempfile.TemporaryDirectory() as folder:
            target,prepared,manifest,result=self.fixture(folder)
            def replace(source,destination,timeout=30):
                if source==prepared:raise PermissionError('simulated locked destination')
                source.replace(destination)
            with patch('updater_helper.replace_with_retry',side_effect=replace):
                self.assertFalse(install(manifest,launcher=Mock()))
            self.assertEqual((target/'SketchfabLocal').read_text(),'old')
            self.assertEqual((target/'user-model.glb').read_text(),'user model')

    def test_manifest_cannot_move_payload_from_outside_its_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            target,prepared,manifest,result=self.fixture(folder)
            data=json.loads(manifest.read_text());data['prepared']=str(target);write_json(manifest,data)
            with self.assertRaises(ValueError):install(manifest)
            self.assertEqual((target/'SketchfabLocal').read_text(),'old')


if __name__=='__main__':unittest.main()
