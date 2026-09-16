import random
import time
from . import proto

def _client_message_id():
    return f"{int(time.time() * 1000)}{random.randint(0, 999):03d}"

class IM:
    def __init__(self, client):
        self.c = client

    def list_conversations(self, cursor="0", count=20):
        r = self.c.get_im("/v1/conversation/list/", {"cursor": cursor, "count": count})
        return self._parse_conv_list(r), "", False

    def get_messages(self, conv_id, cursor="0", count=20):
        r = self.c.get_im("/v1/message/get_by_conversation/",
                          {"conversation_id": conv_id, "cursor": cursor, "count": count})
        return self._parse_messages(r), "", False

    def mark_read(self, conv_id):
        return self.c.post_im("/v3/conversation/mark_read/",
                              proto.encode_fields({1: conv_id.encode()}),
                              {"conversation_id": conv_id})

    def send_text(self, conv_id, text, conversation_type=1, message_type=1,
                  ticket="", client_message_id=""):
        cmid = client_message_id or _client_message_id()
        send_fields = {1: conv_id.encode(), 2: conversation_type,
                       4: text.encode(), 6: message_type, 8: cmid.encode()}
        if ticket:
            send_fields[7] = ticket.encode()
        smb = proto.encode_fields(send_fields)
        request_body = proto.encode_fields({1: smb})
        request = proto.encode_fields({1: 1, 8: request_body})
        return self.c.post_im("/v1/message/send/", request)

    def _parse_conv_list(self, resp):
        return []

    def _parse_messages(self, resp):
        return []
