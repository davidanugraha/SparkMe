"""Native stream assembly across nullable and interleaved tool-call fragments."""
import importlib.util
from types import SimpleNamespace as NS
import unittest


@unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install Qwen dependencies')
class NativeStreamTests(unittest.TestCase):
    def test_nullable_headers_and_interleaved_calls(self):
        from evaluation.agentic_judge.backends.qwen_agent import _native_model
        model = _native_model({'model': 'qwen-test', 'model_type': 'oai',
                              'model_server': 'http://127.0.0.1:1/v1', 'api_key': 'EMPTY'})

        def fragment(index, name, arguments, call_id=None):
            return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))

        def chunk(parts):
            return NS(choices=[NS(delta=NS(content=None, tool_calls=parts))])

        class Stream:
            closed = False

            def __iter__(self):
                return iter([chunk([fragment(0, 'read_', None, 'a')]),
                             chunk([fragment(1, 'review_overview', None, 'b')]),
                             chunk([fragment(0, 'lines', '{"start":1,')]),
                             chunk([fragment(1, None, '{}')]),
                             chunk([fragment(0, None, '"end":1}')])])

            def close(self):
                self.closed = True

        stream = Stream()
        model._chat_complete_create = lambda **kwargs: stream
        outputs = list(model._chat_stream([], False, {}))
        final = outputs[-1]
        self.assertEqual([m.function_call.name for m in final], ['read_lines', 'review_overview'])
        self.assertEqual([m.function_call.arguments for m in final], ['{"start":1,"end":1}', '{}'])
        self.assertEqual([m.extra['function_id'] for m in final], ['a', 'b'])
        self.assertTrue(stream.closed)


if __name__ == '__main__':
    unittest.main()
