"""Mobile signed-API client -- documented alternative, not the shipped path (DESIGN.md §1)."""
import random
import time
from . import proto

def _client_message_id():
    return f"{int(time.time() * 1000)}{random.randint(0, 999):03d}"


# Response{3:status,6:body,7:log} -> ResponseBody{1:send_message_body} ->
# SendMessageResponseBody{1:server_message_id,3:status,4:client_message_id,9:new_ticket}.
# The Response envelope and SendMessageResponseBody field numbers are confirmed
# from the decompiled IM SDK; ResponseBody field 1 is inferred by mirroring the
# request side. status_code 0 at the top level means the call was accepted.
def _parse_send_response(resp, client_message_id):
    out = {"ok": resp.get("status_code") == 0, "log_id": resp.get("log_id"),
           "server_message_id": None, "new_ticket": None,
           "client_message_id": client_message_id}
    body = resp.get("body")
    if not body:
        return out
    rb = proto.decode_fields(body)
    inner = rb.get(1)
    if not inner:
        return out
    smb = proto.decode_fields(inner[0])
    def first(idx):
        v = smb.get(idx)
        return v[0] if v else None
    smid = first(1)
    out["server_message_id"] = smid.decode() if isinstance(smid, bytes) else smid
    tk = first(9)
    out["new_ticket"] = tk.decode() if isinstance(tk, bytes) else tk
    return out

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
        resp = self.c.post_im("/v1/message/send/", request)
        return _parse_send_response(resp, cmid)

    # Response parsing for the mobile path was never reached: the path is blocked by
    # device registration before any conversation list comes back (DESIGN.md §1).
    def _parse_conv_list(self, resp):
        return []

    def _parse_messages(self, resp):
        return []
