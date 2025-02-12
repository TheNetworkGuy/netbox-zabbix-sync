#!/usr/bin/env python3
"""
All of the Zabbix Usermacro related configuration
"""
from logging import getLogger
from zabbix_utils import APIRequestError
from modules.exceptions import UsermacroError


from pprint import pprint

try:
    from config import (
        usermacro_sync,
    )
except ModuleNotFoundError:
    print("Configuration file config.py not found in main directory."
           "Please create the file or rename the config.py.example file to config.py.")
    sys.exit(0)

class ZabbixUsermacros():
    """Class that represents a Zabbix interface."""

    def __init__(self, context, usermacro_map, logger=None):
        self.context = context
        self.usermacro_map = usermacro_map
        self.logger = logger if logger else getLogger(__name__)
        self.usermacros = {}
        self.sync = False
        self.force_sync = False
        self._setConfig()

    def __repr__(self):
        return self.name
  
    def __str__(self):
        return self.__repr__()

    def _setConfig(self):
        if str(usermacro_sync) == "full":
            self.sync = True
            self.force_sync = True
        elif usermacro_sync:
            self.sync = True
        return True
        
    
