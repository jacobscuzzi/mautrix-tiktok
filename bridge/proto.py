def read_varint(buf, i):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7

def _write_varint(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def decode_fields(buf):
    fields = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = read_varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        else:
            break
        fields.setdefault(field, []).append(v)
    return fields

def encode_fields(fields):
    out = bytearray()
    for field, value in fields.items():
        if isinstance(value, int):
            out += _write_varint(field << 3)
            out += _write_varint(value)
        else:
            out += _write_varint((field << 3) | 2)
            out += _write_varint(len(value))
            out += value
    return bytes(out)


def _valid_message(buf):
    # True only if buf parses cleanly as protobuf, consuming every byte with
    # known wire types. Used to decide whether a bytes field is a nested message.
    i, n = 0, len(buf)
    if n == 0:
        return False
    try:
        while i < n:
            tag, i = read_varint(buf, i)
            wt = tag & 7
            if wt == 0:
                _, i = read_varint(buf, i)
            elif wt == 2:
                ln, i = read_varint(buf, i)
                i += ln
            elif wt == 5:
                i += 4
            elif wt == 1:
                i += 8
            else:
                return False
    except IndexError:
        return False
    return i == n


def encode_tree(tree):
    """Encode a decode_tree()-shaped dict {field: [values]} back to protobuf bytes.

    Inverse of decode_tree for the fields we build: nested dict -> length-delimited
    message, str -> utf-8, int -> varint, bytes -> as-is. Field order follows dict
    insertion order (which decode_fields preserves).
    """
    out = bytearray()
    for field, values in tree.items():
        for v in values:
            if isinstance(v, bool):
                out += _write_varint(field << 3) + _write_varint(int(v))
            elif isinstance(v, int):
                out += _write_varint(field << 3) + _write_varint(v)
            else:
                if isinstance(v, dict):
                    b = encode_tree(v)
                elif isinstance(v, str):
                    b = v.encode("utf-8")
                else:
                    b = bytes(v)
                out += _write_varint((field << 3) | 2) + _write_varint(len(b)) + b
    return bytes(out)


def decode_tree(buf, max_depth=6):
    # Recursively decode, treating a length-delimited field as a nested message
    # when it parses cleanly, else as a UTF-8 string, else raw bytes. This is the
    # tool for mapping captured IM response bodies to field numbers.
    out = {}
    for k, vals in decode_fields(buf).items():
        decoded = []
        for v in vals:
            if isinstance(v, int):
                decoded.append(v)
            elif max_depth > 0 and _valid_message(v):
                decoded.append(decode_tree(v, max_depth - 1))
            else:
                try:
                    s = v.decode("utf-8")
                    decoded.append(s if s.isprintable() or s == "" else v)
                except UnicodeDecodeError:
                    decoded.append(v)
        out[k] = decoded
    return out
