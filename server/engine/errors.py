"""内核的错：一律 EngineError，带一句原因（不带令牌、不带对方的原话）。"""
from __future__ import annotations


class EngineError(RuntimeError):
    """kind：config（没配好：不认识的 provider、没有密钥）| http（接口报错）| timeout | output（回的不像样，比如不是要的 JSON）
    | refusal（模型拒答）| aborted（被停了）| max_steps（一轮里步数到顶）| rule（违反回合规矩，比如给外人回合挂了工具）。
    retryable：再来一次可能就好了（429、5xx、连不上、超时）。"""

    def __init__(self, message: str, *, kind: str = "http", retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status
