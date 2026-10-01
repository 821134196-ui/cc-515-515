"""敏感字段遮罩：身份证、手机号，以及证明材料文本的落盘前遮罩。"""
import re

# 18 位身份证（或 15 位老证）：保留前 6 位地址码与后 4 位
_ID_RE = re.compile(r"(?<!\d)(\d{6})\d{8,10}([0-9Xx]{4})(?!\d)")
# 11 位手机号：保留前 3 后 4
_PHONE_RE = re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)")


def mask_id_number(value):
    """110101200201011234 -> 1101**********1234"""
    if not value:
        return ""
    v = str(value)
    if len(v) >= 10:
        return v[:4] + "*" * (len(v) - 8) + v[-4:]
    return v[0] + "*" * (len(v) - 2) + v[-1]


def mask_phone(value):
    """13812345678 -> 138****5678"""
    if not value:
        return ""
    v = str(value)
    if len(v) >= 7:
        return v[:3] + "****" + v[-4:]
    return "*" * len(v)


def mask_text(text):
    """对证明材料正文做落盘前遮罩：身份证号、手机号一律打码。

    本地只保存遮罩后的版本，原始内容从不写入磁盘。
    """
    if not text:
        return ""
    text = _ID_RE.sub(lambda m: m.group(1) + "*" * 10 + m.group(2), text)
    text = _PHONE_RE.sub(lambda m: m.group(1) + "****" + m.group(2), text)
    return text
