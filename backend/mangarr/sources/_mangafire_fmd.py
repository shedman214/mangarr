"""Bundled MangaFire API signing fallback.

Port of the public FMD2 MangaFire module's GenerateVRF / EncryptStage,
https://github.com/dazedcat19/FMD2/blob/master/lua/modules/MangaFire.lua
FMD2 contributors, GNU GPL v2 or later; distributed under this project's GPLv3.
Signing tables verified 2026-11-03. These are public protocol constants, not
account credentials. Dynamic site signing remains preferred for key updates.
"""
import base64

_STAGES = (('yINlmUNho8VYJT+ibTIP+9ESiULpVEtMOoD6U6lRE0R/xwXo/Xp9NrUgC4cw/Lmo33vUyjUE40kUoEWIr/fxfNNcq2s79ShQ5NhNrFnJ4hXPwOu/SuXzIbuTQKGFvfm08E9jvCfqAtoDqvQq3dVWPQFmJjgvkISBeXY3BgANR+yVnjGbcxZ47d6kLNfZPIayTq3/YGySb1KuVZodWp/WGNAO5pfMcpaK53Hhs0allBszaMaxuouOwdxbwgxIw6YunSsXjI05Yi0j9j4eHKfSXR8Ifo/Od+8iamRfCXTyvm7NGRGYdcQ0ywcK/u6RXhrbcCm4t2eCtrDgQVecJGkQ+A==', '0Ec58JOY3uBzJK9m3zqIOpdlF7UFiax9DmA=', 90), ('IUFltCxD3Oc2cwCgkJffthaOg9cgPUb0LgW6H/VtfcF0kc5F25t+aWj6JH9VOhOaY0rAFdUxlDnl5BLNvwEJvQtP5qcw7vdb/K+chnbwnspSHT8mz5lqwz41TezG0hkO06FTjJZhsyNuFLDpD2ZZxQj/QIRcF90zpmQ7Byu483WsQqUE0C342HL+JXngRB6fRzxRyVTaKu83h7UYTJ0QMt6ixFh6S3F8gqkKwrGTL3jHNBsD45UnifK8+RGtishQV2K3rujLKEkiZxpr2dYcudFW4oFsDKhad3CLBvuyTqsCo4B7mL5IKQ1vXo/MOOvq1I1d8ar9X6Ttu5KF4fZgiA==', 'AAdjb1iPY8CiDmq9H34tKTBF8a3oDQ==', 53), ('NQHlu1/wVO5EmkwQymF810qqY2xG1k2obcas4Z9mCsPEIFl9pRIjFxbJ7ybMHbBckT5Ton85E0FOeHezbh/mjlEYpmpnlXOS8dgrqeq2KfxImTh1YK9y0PeMNhzA1OQzSY9brYOJq/l2QnE/hwOeZIhPixVSKIUlDb5vLcH6RWKxkIEMuP0bDwIqQ71AJJaEaMJL7A6YtyIwoRT+L5v4aZzodN/0+3nOGsfblFjgxSfPzVDjNFeNl5P26+kEC/8AHgdrpAbt3hHz3HrRN1Y6e+JHgF7ncFWnoF0y3THL1S71WgWGCa6KtSzTCCG58n68nTyj2T3Sshk7utqCtMi/ZQ==', 'DELOJgPsVaCcblDtTGMdHzM=', 186))


def sign_request(path: str, query: str = "") -> str:
    data = (path + ("?" + query if query else "")).encode("utf-8")
    for table_b64, key_b64, initial in _STAGES:
        table, key = base64.b64decode(table_b64), base64.b64decode(key_b64)
        previous = initial
        output = bytearray()
        for index, value in enumerate(data):
            previous = table[value ^ key[index % len(key)] ^ previous]
            output.append(previous)
        data = bytes(output)
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
