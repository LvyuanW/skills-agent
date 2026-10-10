import base64
import copy
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from skills_agent.agent import Agent
from skills_agent.context.auxiliary import Auxiliary
from skills_agent.context.history import _message_text
from skills_agent.feedback import feedback_content
from skills_agent.providers import OpenAIProvider
from skills_agent.session import Session
from skills_agent.skills import SkillRegistry
from tests.test_agent_context import ScriptedModel, call, final
from tests.test_context_auxiliary import Model
from tests.test_feedback_providers import png_bytes


class FeedbackRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.session = Session(self.root / 'sessions', 'main')
        self.image = self.root / 'preview.wrong-extension'
        self.pixels = png_bytes((225, 90, 30))
        self.image.write_bytes(self.pixels)

    def envelope(self, attachments=None, **extra):
        return {'skills_agent_feedback': 1, 'kind': 'image', 'source': str(self.image),
                'observations': {}, 'attachments': attachments if attachments is not None else [
                    {'type': 'image', 'path': str(self.image), 'mime_type': 'image/jpeg',
                     'label': 'Saved stage'}], **extra}

    def output(self, payload, **extra):
        return json.dumps({'exit_code': 0, 'stdout': json.dumps(payload), 'stderr': '', **extra})

    def command(self, payload):
        return shlex.join([sys.executable, '-c', f'print({json.dumps(payload)!r})'])

    def agent(self, model, **options):
        return Agent(model, self.session, SkillRegistry([]), self.root, **options)

    def test_real_shell_result_delivers_pixels_and_persists_an_immutable_snapshot(self):
        original_task = self.session.make_user_message('Review the actual artifact.', [])

        def inspect(system, messages, tools, on_event):
            result = messages[-1]
            self.assertEqual(result['role'], 'tool')
            image = next(block for block in result['content'] if block['type'] == 'image')
            self.assertEqual(image['mime_type'], 'image/png')
            self.assertEqual(Path(image['path']).read_bytes(), self.pixels)
            self.assertNotEqual(Path(image['path']), self.image)
            # The request reads the saved bytes even after the source is deleted.
            self.image.unlink()
            payload = OpenAIProvider('deepseek-flash', 'unused',
                                     'https://api.deepseek.com')._chat_payload(system, messages, tools)
            native = next(block for block in payload['messages'][-1]['content']
                          if block['type'] == 'image_url')
            self.assertEqual(native['image_url']['url'],
                             'data:image/png;base64,' + base64.b64encode(self.pixels).decode())
            return final('Reviewed the supplied observation.')

        model = ScriptedModel([call('shell', {'name': 'Read saved image',
                              'command': self.command(self.envelope())}), inspect])
        agent = self.agent(model)
        agent.run(original_task)
        self.assertEqual(agent.context.latest_user(), original_task)
        self.assertEqual([message['role'] for message in self.session.messages()],
                         ['user', 'assistant', 'tool', 'assistant'])
        reopened = Session(self.root / 'sessions', 'main').messages()[2]
        image = next(block for block in reopened['content'] if block['type'] == 'image')
        self.assertEqual(Path(image['path']).read_bytes(), self.pixels)
        self.assertIn(image['path'], _message_text(reopened))

        def resumed(system, messages, tools, on_event):
            tool = next(message for message in messages if message['role'] == 'tool')
            saved = next(block for block in tool['content'] if block['type'] == 'image')
            self.assertEqual(Path(saved['path']).read_bytes(), self.pixels)
            return final('Continued from saved feedback.')

        self.agent(ScriptedModel([resumed])).run(self.session.make_user_message('Continue.', []))

    def test_unmarked_stdout_does_not_load_images_and_invalid_feedback_is_explicit(self):
        ordinary = self.output({'attachments': self.envelope()['attachments']})
        self.assertEqual(feedback_content(ordinary, self.session), ordinary)
        invalid = [
            self.envelope(skills_agent_feedback=2),
            self.envelope(skills_agent_feedback=True),
            self.envelope(attachments='not a list'),
            self.envelope(attachments=[{'type': 'file', 'path': str(self.image)}]),
            self.envelope(attachments=[{'type': 'image', 'path': 'relative.png'}]),
            self.envelope(attachments=[{'type': 'image', 'path': str(self.root / 'missing.png')}]),
        ]
        bad = self.root / 'not-image.png'
        bad.write_text('This filename is not image data.')
        invalid.append(self.envelope(attachments=[{'type': 'image', 'path': str(bad)}]))
        for payload in invalid:
            with self.subTest(payload=payload):
                result = json.loads(feedback_content(self.output(payload, exit_code=7,
                                                     stderr='original failure'), self.session))
                self.assertTrue(result['feedback_errors'])
                self.assertEqual(result['exit_code'], 7)
                self.assertEqual(result['stderr'], 'original failure')
        self.assertFalse((self.session.path / 'attachments').exists())

    def test_one_bad_attachment_does_not_silently_hide_the_others_or_the_error(self):
        payload = self.envelope(attachments=[self.envelope()['attachments'][0],
                                {'type': 'image', 'path': str(self.root / 'missing.png')}])
        content = feedback_content(self.output(payload), self.session)
        self.assertEqual(len([block for block in content if block['type'] == 'image']), 1)
        execution = json.loads(content[0]['text'])
        self.assertIn('attachment 2', execution['feedback_errors'][0])
        self.assertEqual(json.loads(execution['stdout']), payload)

    def test_report_and_auto_selection_preserve_native_feedback(self):
        for mode in ('report', 'auto'):
            with self.subTest(mode=mode):
                model = ScriptedModel([])
                agent = self.agent(model)
                payload = self.envelope(observations={'long': 'word ' * 9000})
                content = feedback_content(self.output(payload), self.session)
                original = copy.deepcopy(content)
                record = {'event_id': 'event-feedback', 'message': {'role': 'tool',
                          'tool_call_id': 'feedback', 'content': content}}
                with patch.object(agent.auxiliary, 'tool_report', return_value='A text report.') as report:
                    selected = agent._select(record, {'name': 'shell', 'arguments': json.dumps({
                        'name': 'Review', 'command': 'inspect', 'result_mode': mode})})
                image = next(block for block in selected['message']['content'] if block['type'] == 'image')
                self.assertEqual(Path(image['path']).read_bytes(), self.pixels)
                self.assertEqual(image, next(block for block in original if block['type'] == 'image'))
                self.assertEqual(report.call_args.kwargs['attachments'], [image])
                self.assertIn('A text report.', selected['message']['content'][0]['text'])

    def test_tool_report_receives_actual_images_in_the_existing_counted_request(self):
        content = feedback_content(self.output(self.envelope()), self.session)
        images = [block for block in content if block['type'] == 'image']
        original = copy.deepcopy(images)
        model = Model(['The supplied image is available for review.'])
        auxiliary = Auxiliary(model, self.session)
        auxiliary.tool_report(content[0]['text'], {'name': 'Review'}, {'event_id': 'event-result'},
                              {'current_user_task': {'role': 'user', 'content': 'Inspect the saved stage.'}},
                              attachments=images)
        self.assertEqual(len(model.calls), 1)
        messages = model.calls[0]['messages']
        self.assertIn(messages, model.counted)
        actual = [block for block in messages[0]['content'] if block['type'] == 'image']
        self.assertEqual(actual, images)
        self.assertEqual(Path(actual[0]['path']).read_bytes(), self.pixels)
        self.assertIn('tool_output', messages[0]['content'][1]['text'])
        self.assertEqual(images, original)

    def test_oversized_direct_text_falls_back_without_discarding_the_image(self):
        payload = self.envelope(observations={'large': 'LARGE_FEEDBACK ' * 9000})

        def inspect(system, messages, tools, on_event):
            content = messages[-1]['content']
            self.assertIn('does not fit', content[0]['text'])
            image = next(block for block in content if block['type'] == 'image')
            self.assertEqual(Path(image['path']).read_bytes(), self.pixels)
            return final('Read the retained image.')

        model = ScriptedModel([call('shell', {'name': 'Read direct feedback',
                              'command': self.command(payload), 'result_mode': 'direct'}), inspect])
        model.count_tokens = lambda system, messages, tools: (
            2000 if any('LARGE_FEEDBACK' in _message_text(message)
                        for message in messages if message['role'] == 'tool') else 100)
        self.agent(model, context_tokens=1000).run(self.session.make_user_message('Review.', []))

    def test_compression_and_history_child_receive_native_tool_feedback(self):
        content = feedback_content(self.output(self.envelope()), self.session)
        records = [
            {'event_id': 'event-call', 'message': call('shell', {'name': 'Render', 'command': 'render'})},
            {'event_id': 'event-result', 'message': {'role': 'tool', 'tool_call_id': 'call-1',
                                                  'name': 'shell', 'content': content}},
        ]
        model = Model(['Saved visual feedback is available.'])
        Auxiliary(model, self.session).compress('', records, {'role': 'user', 'content': 'Review.'})
        native = [block for block in model.calls[0]['messages'][0]['content'] if block['type'] == 'image']
        self.assertEqual(len(native), 1)
        self.assertEqual(Path(native[0]['path']).read_bytes(), self.pixels)

        self.session.append(self.session.make_user_message('Review the saved artifact.', []))
        for record in records:
            self.session.append(record['message'])

        def history_child(system, messages, tools, on_event):
            images = [block for block in messages[0]['content'] if block['type'] == 'image']
            self.assertEqual(len(images), 1)
            self.assertEqual(Path(images[0]['path']).read_bytes(), self.pixels)
            return final('The saved observation is available.')

        agent = self.agent(ScriptedModel([history_child]))
        agent.context.sync()
        self.assertEqual(agent._subagent('Review history', 'Inspect the saved feedback.', history=True),
                         'The saved observation is available.')


    def test_native_image_history_preserves_real_multiline_stdout_and_stderr(self):
        from skills_agent.context.history import HistoryIndex

        stdout = json.dumps(self.envelope(), ensure_ascii=False, indent=2) + '\n'
        stderr = 'first diagnostic\nsecond diagnostic\n'
        command = shlex.join([sys.executable, '-c',
                              f'import sys; sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r})'])
        model = ScriptedModel([call('shell', {'name': 'Read multiline image feedback',
                              'command': command, 'result_mode': 'direct'}), final('Reviewed.')])
        self.agent(model).run(self.session.make_user_message('Inspect the captured feedback.', []))
        event = next(event for event in self.session.events()
                     if event.get('type') == 'message' and event['message']['role'] == 'tool')
        message = event['message']
        execution = json.loads(message['content'][0]['text'])
        self.assertEqual(execution['stdout'], stdout)
        self.assertEqual(execution['stderr'], stderr)
        image = next(block for block in message['content'] if block['type'] == 'image')
        self.assertEqual(Path(image['path']).read_bytes(), self.pixels)

        decoded = HistoryIndex(self.session.path).read(event['event_id'])
        self.assertEqual(decoded['text'], _message_text(message))
        self.assertIn('stdout:\n' + stdout, decoded['text'])
        self.assertIn('stderr:\n' + stderr, decoded['text'])
        self.assertIn(image['path'], decoded['text'])
        self.assertGreater(decoded['line_count'], len(stdout.splitlines()))


if __name__ == '__main__':
    unittest.main()
