import hashlib
import unittest
from unittest.mock import patch

import hf_candidate_downloads as downloads

REV='a'*40
SHA=hashlib.sha256(b'gguf').hexdigest()

class CandidateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.inventory={'catalog_id':'hf:org/model','repo_id':'org/model','revision':REV,
                        'free_bytes':30*1024**3,'files':[{'name':'model.Q4_K_M.gguf','size_bytes':4,'sha256':SHA,'revision':REV}]}

    def test_start_requires_exact_revision_and_sha_from_inventory(self):
        with patch.object(downloads,'list_files',return_value=self.inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'manifestem'):
                downloads.start('hf:org/model',[{'name':'model.Q4_K_M.gguf','sha256':'0'*64,'revision':REV}])

    def test_start_rejects_too_many_files(self):
        with patch.object(downloads,'list_files',return_value=self.inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'od 1 do 3'):
                downloads.start('hf:org/model',[{}, {}, {}, {}])

    def test_start_rejects_insufficient_disk_space(self):
        inventory=dict(self.inventory,free_bytes=100)
        with patch.object(downloads,'list_files',return_value=inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'Za mało wolnego miejsca'):
                downloads.start('hf:org/model',[{'name':'model.Q4_K_M.gguf','sha256':SHA,'revision':REV}])

    def test_list_files_uses_only_gguf_with_size_and_sha256_lfs(self):
        info={'sha':REV,'siblings':[
            {'rfilename':'model.gguf','size':4,'lfs':{'sha256':SHA}},
            {'rfilename':'README.md','size':20},
            {'rfilename':'../escape.gguf','size':4,'lfs':{'sha256':SHA}},
            {'rfilename':'unknown.gguf','size':4},
        ]}
        with patch.object(downloads.audit,'_candidate',return_value={'repo_id':'org/model','revision':REV}), \
             patch.object(downloads.audit,'_request',return_value=__import__('json').dumps(info).encode()):
            result=downloads.list_files('hf:org/model')
        self.assertEqual([x['name'] for x in result['files']],['model.gguf'])
        self.assertEqual(result['files'][0]['sha256'],SHA)

    def test_redirect_rejects_non_huggingface_hosts(self):
        handler=downloads._Redirects()
        with self.assertRaisesRegex(downloads.DownloadError,'przekierowanie'):
            handler.redirect_request(None,None,302,'Found',{},'https://evil.example/file')

if __name__=='__main__': unittest.main()
