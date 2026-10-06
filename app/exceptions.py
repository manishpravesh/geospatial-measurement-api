class RejectedUpload(Exception):
    """The request should not be stored. `status_code` is what the route returns."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class FileContentError(Exception):
    """The upload was the right kind of file, but the contents could not be used."""
