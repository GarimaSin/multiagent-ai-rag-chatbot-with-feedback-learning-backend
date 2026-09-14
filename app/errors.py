class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class ProviderError(Exception):
    def __init__(self, code: str = "provider_unavailable", retryable: bool = True):
        self.code, self.retryable = code, retryable
        super().__init__(code)
