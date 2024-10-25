"""Module that hosts all functions for virtual machine processing"""

from modules.exceptions import *

class VirtualMachine():
    """Model for virtual machines"""
    def __init__(self, nb, name):
        self.nb = nb
        self.name = name
        self.hostgroup = None

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _data_prep(self):
        self.platform = self.nb.platform.name
        self.cluster = self.nb.cluster.name

    def set_hostgroup(self):
        self.hostgroup = "Virtual machines"
