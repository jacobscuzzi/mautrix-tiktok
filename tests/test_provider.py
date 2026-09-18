import unittest

from bridge.provider import MessageProvider, NativeProvider, describe
from bridge.im import IM


class FakeClient:
    def get_im(self, path, params=None):
        return {"status_code": 0, "body": b"", "raw": {}}

    def post_im(self, path, body, params=None):
        return {"status_code": 0, "body": None, "raw": {}}


class TestProviderSeam(unittest.TestCase):
    def test_native_im_satisfies_provider(self):
        im = IM(FakeClient())
        self.assertIsInstance(im, MessageProvider)

    def test_native_provider_alias_is_im(self):
        self.assertIs(NativeProvider, IM)

    def test_describe_labels_native(self):
        self.assertEqual(describe(IM(FakeClient())), "native")

    def test_provider_requires_all_four_methods(self):
        class Partial:
            def list_conversations(self, cursor="0", count=20):
                return [], "", False

        # missing get_messages/send_text/mark_read -> not a provider
        self.assertNotIsInstance(Partial(), MessageProvider)


if __name__ == "__main__":
    unittest.main()
