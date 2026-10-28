"""Domain errors; each carries the HTTP status the web layer maps it to."""


class WallmanError(Exception):
    http_status = 500


class InvalidImage(WallmanError):
    http_status = 400


class TooSmall(WallmanError):
    http_status = 422


class RatioNeedsCrop(WallmanError):
    http_status = 422


class NameConflict(WallmanError):
    http_status = 409


class LicenseNeedsDownload(WallmanError):
    http_status = 422


class NotShareable(WallmanError):
    http_status = 422


class UploadTooLarge(WallmanError):
    http_status = 413


class DownloadFailed(WallmanError):
    http_status = 502


class ChecksumMismatch(WallmanError):
    http_status = 502


class GitError(WallmanError):
    http_status = 500


class PathOutsideRepo(WallmanError):
    http_status = 400


class NotFound(WallmanError):
    http_status = 404
