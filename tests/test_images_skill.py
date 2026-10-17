import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from skills_agent.skills import SkillRegistry
from tests.test_feedback_script import BUILTIN_ROOT, SCRIPT, png


IMAGE_SCRIPT = BUILTIN_ROOT / 'images' / 'scripts' / 'images.py'


def load_images_helper():
    spec = importlib.util.spec_from_file_location('images_skill_helper', IMAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sized_png(size):
    """Make an actual PNG with a legal ancillary text chunk at an exact size."""
    original = png(2, 2)
    text = b'Note\0' + b'x' * (size - len(original) - 17)
    chunk = (struct.pack('>I', len(text)) + b'tEXt' + text
             + struct.pack('>I', zlib.crc32(b'tEXt' + text)))
    return original[:-12] + chunk + original[-12:]


class ImagesSkillTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()

    def run_helper(self, *args):
        # Isolated Python proves the helper does not need skills_agent installed.
        result = subprocess.run([sys.executable, '-I', str(SCRIPT), *map(str, args)],
                                cwd=self.workspace, capture_output=True, timeout=15)
        return result, json.loads(result.stdout)

    def test_images_skill_is_discovered_from_builtin_root(self):
        catalog = SkillRegistry([BUILTIN_ROOT]).catalog()
        self.assertIn(str(BUILTIN_ROOT / 'images' / 'SKILL.md'), catalog)
        self.assertIn('- images:', catalog)

    def test_exact_file_byte_limit_accepts_one_mb_despite_base64_expansion(self):
        path = self.workspace / 'exact image.png'
        original = sized_png(1_000_000)
        path.write_bytes(original)
        result, data = self.run_helper('image', path)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(path.read_bytes()), 1_000_000)
        self.assertGreater(len(base64.b64encode(original)), 1_000_000)
        self.assertEqual(data['attachments'][0]['url'], path.as_uri())
        self.assertEqual(path.read_bytes(), original)
        self.assertLess(len(result.stdout), 3000)

    def test_oversized_existing_image_fails_without_attachment_or_source_mutation(self):
        path = self.workspace / 'oversized.png'
        original = sized_png(1_000_001)
        path.write_bytes(original)
        result, data = self.run_helper('image', path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['attachments'], [])
        self.assertIn('1000000', data['observations']['error'])
        self.assertIn('compress', data['observations']['error'])
        self.assertEqual(path.read_bytes(), original)

    def test_ten_images_pass_but_eleven_fail_without_partial_attachment(self):
        paths = [self.workspace / f'image-{i}.png' for i in range(11)]
        for path in paths:
            path.write_bytes(png())
        result, data = self.run_helper('image', *paths[:10])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([item['url'] for item in data['attachments']], [p.as_uri() for p in paths[:10]])
        result, data = self.run_helper('image', *paths)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['attachments'], [])
        self.assertIn('10', data['observations']['error'])

    def test_source_urls_remain_distinct_from_sent_local_urls(self):
        paths = [self.workspace / 'first.png', self.workspace / 'second.png']
        for path in paths:
            path.write_bytes(png())
        sources = ['https://example.test/original.png', (self.workspace / 'original.png').as_uri()]
        result, data = self.run_helper('image', *paths, '--source-url', sources[0], '--source-url', sources[1])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([item['source_url'] for item in data['attachments']], sources)
        self.assertEqual([item['url'] for item in data['attachments']], [p.as_uri() for p in paths])

    def test_invalid_or_ambiguous_source_url_fails_as_feedback(self):
        path = self.workspace / 'image.png'
        path.write_bytes(png())
        for args in [(path, path, '--source-url', 'https://example.test/image.png'),
                     (path, '--source-url', 'relative/image.png'),
                     (path, '--source-url', 'https://[invalid')]:
            with self.subTest(args=args):
                result, data = self.run_helper('image', *args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(data['attachments'], [])
                self.assertIn('--source-url', data['observations']['error'])

    @unittest.skipUnless(os.name == 'posix', 'Executable browser fixture needs POSIX permissions')
    def test_render_rejects_oversized_capture_before_replacing_existing_output(self):
        html = self.workspace / 'page.html'
        html.write_text('<p>test page</p>')
        original = self.workspace / 'oversized-source.png'
        original.write_bytes(sized_png(1_000_001))
        output = self.workspace / 'capture.png'
        previous = png(3, 2)
        output.write_bytes(previous)
        browser = self.workspace / 'fake-browser'
        browser.write_text(
            f'#!{sys.executable}\n'
            'import sys\nfrom pathlib import Path\n'
            'target = next(x.split("=",1)[1] for x in sys.argv if x.startswith("--screenshot="))\n'
            f'Path(target).write_bytes(Path({str(original)!r}).read_bytes())\n'
        )
        browser.chmod(0o755)
        result, data = self.run_helper('render', html, '--output', output, '--browser', browser)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['attachments'], [])
        self.assertIn('1000000', data['observations']['error'])
        self.assertEqual(output.read_bytes(), previous)


class ImagesPreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()

    def run_helper(self, *args):
        result = subprocess.run([sys.executable, '-I', str(IMAGE_SCRIPT), *map(str, args)],
                                cwd=self.workspace, capture_output=True, timeout=45)
        return result, json.loads(result.stdout)

    def source(self, name='source.png', size=(96, 64), color='red'):
        path = self.workspace / name
        Image.new('RGB', size, color).save(path)
        return path

    def noise(self):
        path = self.workspace / 'original-noise.png'
        Image.frombytes('RGB', (1400, 1100), random.Random(31).randbytes(1400 * 1100 * 3)).save(path)
        self.assertGreater(path.stat().st_size, 1_000_000)
        return path

    def test_prepare_compresses_large_existing_image_and_retains_original(self):
        source = self.noise()
        digest = hashlib.sha256(source.read_bytes()).digest()
        output = self.workspace / 'derived.jpg'
        result, data = self.run_helper('prepare', source, '--output', output,
                                       '--source-url', 'https://example.test/original.png')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
        self.assertLessEqual(output.stat().st_size, 1_000_000)
        self.assertEqual(data['observations']['bytes'], output.stat().st_size)
        self.assertEqual(data['observations']['source_urls'], ['https://example.test/original.png'])
        self.assertEqual(data['attachments'][0]['url'], output.as_uri())
        self.assertEqual(data['attachments'][0]['mime_type'], 'image/jpeg')
        with Image.open(output) as prepared:
            self.assertEqual(prepared.format, 'JPEG')
            self.assertEqual(list(prepared.size), data['observations']['dimensions'])
            self.assertAlmostEqual(prepared.width / prepared.height, 1400 / 1100, places=2)
        self.assertEqual(set(self.workspace.iterdir()), {source, output})

    def test_prepare_png_resizes_as_needed_and_webp_matches_its_extension(self):
        source = self.noise()
        output = self.workspace / 'small.png'
        result, data = self.run_helper('prepare', source, '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(data['attachments'][0]['mime_type'], 'image/png')
        self.assertLessEqual(output.stat().st_size, 1_000_000)
        with Image.open(output) as prepared:
            self.assertEqual(prepared.format, 'PNG')
            self.assertLess(prepared.width, 1400)
            self.assertAlmostEqual(prepared.width / prepared.height, 1400 / 1100, places=2)
        result, data = self.run_helper('prepare', self.source('flat.png'),
                                       '--output', self.workspace / 'flat.webp')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(data['attachments'][0]['mime_type'], 'image/webp')
        with Image.open(self.workspace / 'flat.webp') as prepared:
            self.assertEqual(prepared.format, 'WEBP')

    def test_uri_paths_are_decoded_once_from_any_working_directory(self):
        source = self.source('中文 100%20 % image.png')
        output = self.workspace / 'prepared.png'
        result, data = self.run_helper('prepare', source.as_uri(), '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(data['observations']['sources'][0]['path'], str(source))
        self.assertEqual(data['attachments'][0]['source_url'], source.as_uri())
        result, data = self.run_helper('attach', source.as_uri())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(data['attachments'][0]['path'], str(source))

    def test_compose_more_than_ten_sources_is_one_image_with_panel_mapping(self):
        sources = [self.source(f'source-{i}.png', (70 + i * 7, 50 + i * 3),
                               color=(i * 20, 30, 210)) for i in range(11)]
        originals = [path.read_bytes() for path in sources]
        output = self.workspace / 'overview.png'
        result, data = self.run_helper('compose', *(path.as_uri() for path in sources),
                                       '--output', output, '--columns', 3)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(data['attachments']), 1)
        self.assertLessEqual(output.stat().st_size, 1_000_000)
        observations = data['observations']
        self.assertEqual(observations['source_urls'], [path.as_uri() for path in sources])
        self.assertEqual([p['number'] for p in observations['panels']], list(range(1, 12)))
        with Image.open(output) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(list(image.size), observations['dimensions'])
            for index, panel in enumerate(observations['panels']):
                left, top, right, bottom = panel['image_box']
                self.assertTrue(0 <= left < right <= image.width)
                self.assertTrue(0 <= top < bottom <= image.height)
                self.assertEqual(panel['source_url'], sources[index].as_uri())
                self.assertAlmostEqual((right - left) * panel['scale_to_source'][0], 70 + index * 7)
                self.assertAlmostEqual((bottom - top) * panel['scale_to_source'][1], 50 + index * 3)
                self.assertEqual(image.getpixel((round((left + right) / 2), round((top + bottom) / 2))),
                                 (index * 20, 30, 210))
                pl, pt, _, _ = panel['panel_box']
                strip = image.crop((pl, pt, pl + 48, pt + 28))
                self.assertGreater(len(strip.getcolors(maxcolors=10_000)), 1)
            # Numbered labels differ, rather than repeating a generic panel label.
            self.assertNotEqual(image.crop((0, 0, 48, 28)).tobytes(),
                                image.crop((512, 0, 560, 28)).tobytes())
        self.assertEqual([path.read_bytes() for path in sources], originals)
        self.assertEqual(set(self.workspace.iterdir()), {*sources, output})

    def test_attach_uses_shared_size_and_count_limits(self):
        source = self.source()
        result, data = self.run_helper('attach', *([source] * 10))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(len(data['attachments']), 10)
        result, data = self.run_helper('attach', *([source] * 11))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['attachments'], [])
        self.assertIn('compose', data['observations']['error'])
        source.write_bytes(sized_png(1_000_001))
        result, data = self.run_helper('attach', source)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(data['attachments'], [])
        self.assertIn('1000000', data['observations']['error'])

    def test_failed_prepare_preserves_existing_output_and_rejects_source_overwrite(self):
        source = self.source()
        original = source.read_bytes()
        output = self.source('previous.png', color='blue')
        previous = output.read_bytes()
        cases = [
            ('prepare', self.workspace / 'missing.png', '--output', output),
            ('prepare', source, '--output', output, '--source-url', 'not-a-url'),
            ('prepare', source, '--output', source),
            ('prepare', source, '--output', self.workspace / 'wrong.gif'),
            ('compose', source, '--output', output, '--columns', 2),
            ('prepare', 'https://example.test/image.png', '--output', output),
        ]
        for args in cases:
            with self.subTest(args=args):
                result, data = self.run_helper(*args)
                self.assertEqual(result.returncode, 1, result.stderr.decode())
                self.assertEqual(data['attachments'], [])
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(set(self.workspace.iterdir()), {source, output})

    def test_generated_image_encodes_only_in_memory_until_bounded(self):
        helper = load_images_helper()
        image = Image.frombytes('RGB', (1400, 1100), random.Random(31).randbytes(1400 * 1100 * 3))
        output = self.workspace / 'generated.jpg'
        real_save = Image.Image.save
        encodings = []

        def recording_save(instance, target, *args, **kwargs):
            self.assertIsInstance(target, io.BytesIO)
            real_save(instance, target, *args, **kwargs)
            encodings.append(len(target.getvalue()))

        real_write = helper.save_image

        def bounded_write(path, encoded, *args, **kwargs):
            self.assertLessEqual(len(encoded), 1_000_000)
            self.assertFalse(path.exists())
            self.assertEqual(list(self.workspace.iterdir()), [])
            return real_write(path, encoded, *args, **kwargs)

        with patch.object(Image.Image, 'save', recording_save), patch.object(helper, 'save_image', bounded_write):
            data = helper.save_bounded(image, output, label='Generated plot')
        self.assertGreater(encodings[0], 1_000_000)
        self.assertEqual(data['attachments'][0]['path'], str(output))
        self.assertLessEqual(output.stat().st_size, 1_000_000)
        self.assertEqual(list(self.workspace.iterdir()), [output])

    def test_bounded_encoder_failure_never_replaces_output(self):
        helper = load_images_helper()
        output = self.source('prior.png')
        previous = output.read_bytes()
        with patch.object(helper.feedback, 'MAX_IMAGE_BYTES', 1):
            with self.assertRaisesRegex(helper.FeedbackError, 'bounded compression'):
                helper.save_bounded(Image.new('RGB', (128, 96), 'red'), output)
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(self.workspace.iterdir()), [output])


if __name__ == '__main__':
    unittest.main()
