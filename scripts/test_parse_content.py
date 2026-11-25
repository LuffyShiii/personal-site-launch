#!/usr/bin/env python3
"""Behavior tests for the read-only template converter; no network or live site."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from parse_content import inspect

PNG = bytes.fromhex('89504e470d0a1a0a')  # Locator tests only; image decoding belongs to the site pipeline.


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='personal-site-skill-')
        self.folder = Path(self.temp.name) / '中文作品 with spaces'
        self.folder.mkdir()
        self.file = self.folder / '说明.txt'
        self.cover = self.folder / 'youtube 封面.png'
        self.cover.write_bytes(PNG)
        self.base = '标题：示例作品\n简介：第一行\n第二行，可以写普通文本。\n链接：https://example.com/work\n精选位置：不精选\n发布状态：发布\n'
        self.write(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, text):
        self.file.write_text(text, encoding='utf-8')

    def fields(self, result):
        return {x['field'] for x in result['issues']}

    def test_unicode_spaces_multiline_and_auto_cover(self):
        r = inspect(self.file)
        self.assertEqual(r['state'], 'ready')
        self.assertEqual(r['content']['cover'], self.cover.name)
        self.assertIn('\n第二行', r['content']['description']['zh'])
        self.assertFalse(r['publishes'])

    def test_english_template_and_case_insensitive_values(self):
        self.write('Title: Example\nDescription: A useful project\nURL: https://example.com/work\nFeatured: NO\nStatus: PUBLISHED\n')
        r = inspect(self.file, ('en',))
        self.assertEqual(r['state'], 'ready')
        self.assertEqual(r['content']['title']['en'], 'Example')

    def test_bilingual_missing_english_is_pending(self):
        r = inspect(self.file, ('zh', 'en'))
        self.assertEqual(r['state'], 'needs_input')
        self.assertIn('title.en', self.fields(r))
        self.assertIn('description.en', self.fields(r))

    def test_bilingual_complete(self):
        self.write(self.base+'英文标题：Example\n英文简介：English summary\n')
        self.assertEqual(inspect(self.file, ('zh', 'en'))['state'], 'ready')

    def test_missing_title_reports_action(self):
        self.write(self.base.replace('标题：示例作品', '标题：'))
        r = inspect(self.file)
        self.assertEqual(r['state'], 'needs_input')
        self.assertTrue(next(x for x in r['issues'] if x['field']=='title.zh')['action'])

    def test_missing_cover_stops_publish(self):
        self.cover.unlink()
        self.assertIn('cover', self.fields(inspect(self.file)))

    def test_multiple_covers_require_choice(self):
        (self.folder/'另一张.jpg').write_bytes(b'image')
        self.assertEqual(inspect(self.file)['state'], 'needs_input')
        self.write(self.base+f'封面：{self.cover.name}\n')
        self.assertEqual(inspect(self.file)['state'], 'ready')

    def test_cover_traversal_rejected(self):
        for name in ['../cover.png', 'C:\\private\\cover.png', '/tmp/cover.png']:
            with self.subTest(name=name):
                self.write(self.base+f'封面：{name}\n')
                self.assertIn('cover', self.fields(inspect(self.file)))

    def test_symlink_cover_rejected(self):
        if sys.platform=='win32':
            self.skipTest('Symlink privileges vary on Windows; verify in target environment')
        link=self.folder/'链接.png';link.symlink_to(self.cover)
        self.write(self.base+f'封面：{link.name}\n')
        self.assertEqual(inspect(self.file)['state'], 'needs_input')

    def test_duplicate_alias_cannot_silently_overwrite(self):
        self.write(self.base+'中文标题：另一标题\n')
        r=inspect(self.file)
        self.assertEqual(r['state'], 'needs_input')
        self.assertEqual(r['content']['title']['zh'], '示例作品')

    def test_bad_links_and_credentials_are_rejected(self):
        for url in ['', 'http://example.com', 'javascript:alert(1)', 'https://u:p@example.com', 'https://example.com/has space', 'https://example.com:bad']:
            with self.subTest(url=url):
                self.write(self.base.replace('https://example.com/work', url))
                self.assertIn('url', self.fields(inspect(self.file)))

    def test_optional_date_is_not_invented(self):
        self.assertIsNone(inspect(self.file)['content']['date'])
        self.write(self.base+'发布日期：2025-02-30\n')
        self.assertIn('date', self.fields(inspect(self.file)))

    def test_site_specific_featured_limit(self):
        self.write(self.base.replace('不精选', '6'))
        self.assertEqual(inspect(self.file, featured_limit=6)['state'], 'ready')
        self.assertEqual(inspect(self.file, featured_limit=5)['state'], 'needs_input')

    def test_draft_default_does_not_publish_complete_content(self):
        self.write(self.base.replace('发布状态：发布\n',''))
        self.assertEqual(inspect(self.file)['state'], 'draft')

    def test_incomplete_draft_still_lists_missing_information(self):
        self.write('发布状态：草稿\n')
        r=inspect(self.file)
        self.assertEqual(r['state'], 'needs_input')
        self.assertEqual(r['content']['status'], 'draft')
        self.assertIn('title.zh', self.fields(r))

    def test_explicit_withdrawal_does_not_need_publish_fields(self):
        self.write('发布状态：下架\n')
        self.assertEqual(inspect(self.file)['state'], 'withdrawn')

    def test_unrecognized_status_does_not_publish(self):
        self.write(self.base.replace('发布状态：发布', '发布状态：maybe'))
        self.assertIn('status', self.fields(inspect(self.file)))

    def test_bom_and_windows_line_endings(self):
        for encoding in ['utf-8-sig', 'utf-16']:
            with self.subTest(encoding=encoding):
                self.file.write_bytes(self.base.replace('\n','\r\n').encode(encoding))
                self.assertEqual(inspect(self.file)['state'], 'ready')

    def test_unknown_field_outside_body_reported(self):
        self.write(self.base+'链接拼错了：https://example.com\n')
        self.assertIn('text', self.fields(inspect(self.file)))

    def test_unsupported_language_does_not_pretend_compatible(self):
        self.assertEqual(inspect(self.file, ('fr',))['state'], 'needs_input')

    def test_cli_exit_codes_read_only_and_literal_shell_text(self):
        self.write(self.base.replace('示例作品', 'Example $(touch SHOULD_NOT_EXIST)'))
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        tool=Path(__file__).with_name('parse_content.py')
        r=subprocess.run([sys.executable,str(tool),str(self.file)],capture_output=True,encoding='utf-8',timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)['state'],'ready')
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()})
        self.write('标题：缺资料\n发布状态：发布\n')
        r=subprocess.run([sys.executable,str(tool),str(self.file)],capture_output=True,encoding='utf-8',timeout=5)
        self.assertEqual(r.returncode,2)
        self.assertEqual(json.loads(r.stdout)['state'],'needs_input')


if __name__=='__main__':
    unittest.main(verbosity=2)
