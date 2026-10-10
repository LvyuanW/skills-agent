import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills_agent/builtin_skills/feedback/scripts/check.py'


class FeedbackCheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def check(self, *arguments):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, arguments)],
                                capture_output=True, text=True, timeout=10)
        return result, json.loads(result.stdout)['observations']

    def test_file_missing_empty_and_actual_size_have_distinct_results(self):
        target = self.root / 'saved.txt'
        result, record = self.check('file', target)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(record['passed'])
        target.write_bytes(b'')
        result, record = self.check('file', target, '--min-bytes', 1)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(record['checks'][0]['passed'])
        self.assertEqual(record['checks'][1]['actual'], 0)
        target.write_bytes(b'content')
        result, record = self.check('file', target, '--min-bytes', 7)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(record['passed'])

    def test_actual_text_supports_multiline_unicode_and_preserves_source(self):
        path = self.root / 'saved.txt'
        contents = 'header\r\n中文\r\nactual value\r\n'.encode('utf-8')
        path.write_bytes(contents)
        result, record = self.check('text', path, '--contains', '中文\r\nactual value', '--absent', 'draft')
        self.assertEqual(result.returncode, 0)
        self.assertEqual((record['checks'][0]['line'], record['checks'][0]['column']), (2, 1))
        self.assertEqual(path.read_bytes(), contents)
        result, record = self.check('text', path, '--contains', 'remembered value', '--absent', 'actual value')
        self.assertEqual(result.returncode, 1)
        self.assertTrue(all(not check['passed'] for check in record['checks']))

    def test_json_nested_pointer_escaping_and_object_order(self):
        path = self.root / 'result.json'
        path.write_text(json.dumps({'a/b': {'~key': [{'second': 2, 'first': 1}]}}))
        result, record = self.check('json', path, '--pointer', '/a~1b/~0key/0',
                                    '--equals-json', '{"first":1,"second":2}')
        self.assertEqual(result.returncode, 0)
        self.assertTrue(record['passed'])

    def test_missing_value_is_not_null_and_booleans_are_not_numbers(self):
        path = self.root / 'result.json'
        path.write_text('{"null":null,"enabled":true,"nested":{"enabled":true}}')
        for pointer, expected, code in [('/null', 'null', 0), ('/missing', 'null', 1),
                                        ('/enabled', '1', 1), ('/nested', '{"enabled":1}', 1)]:
            with self.subTest(pointer=pointer):
                result, record = self.check('json', path, '--pointer', pointer, '--equals-json', expected)
                self.assertEqual(result.returncode, code)
                self.assertEqual(record['passed'], code == 0)

    def test_acquisition_and_invalid_conditions_are_errors_not_passing_checks(self):
        text = self.root / 'invalid.txt'
        text.write_bytes(b'bad\x00text')
        value = self.root / 'invalid.json'
        value.write_text('{"x":NaN}')
        valid = self.root / 'valid.json'
        valid.write_text('{"x":1}')
        for arguments in [('text', text, '--contains', 'bad'), ('text', text),
                          ('json', value, '--equals-json', 'null'),
                          ('json', valid, '--pointer', '/x~2', '--equals-json', '1'),
                          ('text', self.root / 'missing.txt', '--contains', 'value')]:
            with self.subTest(arguments=arguments):
                result, record = self.check(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(record['passed'])
                self.assertTrue(record['error'])
                self.assertTrue(result.stderr)

    def test_large_json_observation_is_bounded_but_compared_in_full(self):
        path = self.root / 'large.json'
        actual = 'a' * 6000
        path.write_text(json.dumps(actual))
        result, record = self.check('json', path, '--equals-json', json.dumps(actual[:-1] + 'b'))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(record['checks'][0]['actual']['truncated'])
        self.assertLess(len(result.stdout), 3500)

    def test_numeric_comparison_uses_actual_decimal_values_without_float_rounding(self):
        path = self.root / 'numbers.json'
        path.write_text('{"large":9007199254740993.0,"tiny":0.000000000000000000000000000001,'
                        '"huge":1e309,"nested":[{"n":0.10000000000000000001}]}')
        cases = [('/large', '9007199254740992', 1), ('/large', '9007199254740993', 0),
                 ('/tiny', '0', 1), ('/tiny', '1e-30', 0), ('/huge', '1e309', 0),
                 ('/nested', '[{"n":0.1}]', 1), ('/nested', '[{"n":0.10000000000000000001}]', 0)]
        for pointer, expected, code in cases:
            with self.subTest(pointer=pointer, expected=expected):
                result, record = self.check('json', path, '--pointer', pointer, '--equals-json', expected)
                self.assertEqual(result.returncode, code)
                self.assertEqual(record['passed'], code == 0)
        result, record = self.check('json', path, '--pointer', '/large', '--equals-json', '9007199254740993')
        self.assertEqual(record['checks'][0]['actual']['text'], '9007199254740993.0')

    def test_text_locations_follow_crlf_cr_and_lf_source_lines(self):
        path = self.root / 'lines.txt'
        for ending in ('\r\n', '\r', '\n'):
            with self.subTest(ending=repr(ending)):
                path.write_bytes(('first' + ending + '中文' + ending + 'third').encode('utf-8'))
                result, record = self.check('text', path, '--contains', 'third')
                self.assertEqual(result.returncode, 0)
                self.assertEqual((record['checks'][0]['line'], record['checks'][0]['column']), (3, 1))


if __name__ == '__main__':
    unittest.main()
