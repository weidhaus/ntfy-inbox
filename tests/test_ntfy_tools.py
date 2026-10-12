import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ntfy_tools import register_ntfy


class FakeMcp:
    def __init__(self):self.functions={}
    def tool(self):
        def decorator(fn):self.functions[fn.__name__]=fn;return fn
        return decorator


class NtfyToolTests(unittest.TestCase):
    def test_each_tool_checks_its_grant(self):
        mcp=FakeMcp()
        def deny(name):raise PermissionError(name)
        register_ntfy(mcp,deny)
        args={'ntfy_status':{},'ntfy_list_topics':{},'ntfy_get_messages':{},'ntfy_search_reports':{'query':'backup'}}
        for name,arguments in args.items():
            with self.assertRaises(PermissionError):mcp.functions[name](**arguments)

    def test_user_topic_is_query_encoded_and_limits_checked(self):
        mcp=FakeMcp();register_ntfy(mcp,lambda name:None)
        with patch('ntfy_tools.request_archive',return_value={}) as request:
            mcp.functions['ntfy_get_messages'](topic='topic&limit=999',limit=20)
            self.assertIn('topic=topic%26limit%3D999',request.call_args.args[0])
        with self.assertRaises(ValueError):mcp.functions['ntfy_get_messages'](limit=101)
        with self.assertRaises(ValueError):mcp.functions['ntfy_search_reports'](query='')
        with self.assertRaises(ValueError):mcp.functions['ntfy_get_messages'](since=-1)
