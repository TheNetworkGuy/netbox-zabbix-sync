"""
All custom exceptions used for Exception generation
"""


class SyncError(Exception):
    """Class SyncError"""


class JournalError(Exception):
    """Class SyncError"""


class SyncExternalError(SyncError):
    """Class SyncExternalError"""


class SyncInventoryError(SyncError):
    """Class SyncInventoryError"""


class SyncDuplicateError(SyncError):
    """Class SyncDuplicateError"""


class EnvironmentVarError(SyncError):
    """Class EnvironmentVarError"""


class InterfaceConfigError(SyncError):
    """Class InterfaceConfigError"""


class ProxyConfigError(SyncError):
    """Class ProxyConfigError"""


class HostgroupError(SyncError):
    """Class HostgroupError"""


class TemplateError(SyncError):
    """Class TemplateError"""


class UsermacroError(SyncError):
    """Class UsermacroError"""
