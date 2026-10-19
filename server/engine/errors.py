"""内核的错：一律 EngineError，带一句原因（不带令牌、不带对方的原话）。各家回的报错原文可能带着密钥或者它自己打了码的密钥
（DeepSeek 的 401：「Your api key: ****abcd is invalid」，末 4 位是真的）：进库、进事件之前过一遍 scrub。"""
from __future__ import annotations

import re

# 像密钥的串（照 embeddings.py 的 _KEYISH，多认几种）：常见几家的前缀 + 一长串（前缀前面紧挨着字母数字的不算：task-…、disk-… 是普通的词）；
# 各家打了码的（一串 * 连着露出来的几位，比如 ****abcd、sk-proj-****wxyz、abcd****wxyz）整段抹掉；Authorization 里的 Bearer 令牌
KEYISH = re.compile(r"(?<![A-Za-z0-9])(?:sk-ant-|sk-|sk_|gsk_|xai-|AIza|ya29\.)[A-Za-z0-9_\-.]{8,}"
                    r"|[A-Za-z0-9_\-.]*[*•]{3,}[A-Za-z0-9_\-]*"
                    r"|\b[Bb]earer\s+[A-Za-z0-9._~+/\-]{8,}=*")


def scrub(text: str, *secrets: str) -> str:
    """报错的字：抹掉给的密钥（原样的、转义过的）和像密钥的串，换成 ***。不截断（调用方自己截）。"""
    s = str(text or "")
    for sec in secrets:
        core = str(sec or "").strip()
        if len(core) >= 4:
            for form in {core, repr(core.encode())[2:-1], repr(core)[1:-1]}:
                s = s.replace(form, "***")
    return KEYISH.sub("***", s)


class EngineError(RuntimeError):
    """kind：config（没配好：不认识的 provider、没有密钥）| http（接口报错）| timeout | output（回的不像样，比如不是要的 JSON）
    | refusal（模型拒答）| aborted（被停了）| max_steps（一轮里步数到顶）| rule（违反回合规矩，比如给外人回合挂了工具）。
    retryable：再来一次可能就好了（429、5xx、连不上、超时）。
    provider / key：出错的是哪一家、哪一把密钥（providers.stream 填上，备用链按它冷却；key 是那一把的记号：变量名 + 值的
    短指纹，不是密钥本身）。"""

    def __init__(self, message: str, *, kind: str = "http", retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status
        self.provider: str | None = None
        self.key: str | None = None
