"""Custom exceptions for the data acquisition module."""


class NameIdExtractionError(RuntimeError):
    """HTML 页面解析成功但正则未找到 item_nameid 时抛出。
    区别于 ValueError，方便调用方精确 catch。"""


class NameIdNotInitializedError(RuntimeError):
    """fetch_order_book() 被调用但 item_nameid 尚未预解析时抛出。
    这是启动顺序错误，而非网络错误；错误信息应指引用户运行 NameIdInitializer。"""
