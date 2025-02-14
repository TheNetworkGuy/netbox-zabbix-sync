#!/usr/bin/env python3
"""
All of the Zabbix Usermacro related configuration
"""
from re import match
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
        if str(usermacro_sync).lower() == "full":
            self.sync = True
            self.force_sync = True
        elif usermacro_sync:
            self.sync = True
        return True

    def validate_macro(self, macro_name):
        pattern = '\{\$[A-Z0-9\._]*(\:.*)?\}'
        return match(pattern, macro_name)

    def render_macro(self, macro_name, macro_properties):
        macro={}
        macrotypes={'text': 0, 'secret': 1, 'vault': 2}
        if self.validate_macro(macro_name):
            macro['macro'] = str(macro_name)
            if isinstance(macro_properties, dict):
                if not 'value' in macro_properties:
                   self.logger.error(f'Usermacro {macro_name} has no value, skipping.')
                   return False
                else: 
                    macro['value'] = macro_properties['value']

                if 'type' in macro_properties and macro_properties['type'].lower() in macrotypes:
                    macro['type'] = str(macrotypes[macro_properties['type']])
                else:
                    macro['type'] = str(0)

                if 'description' in macro_properties and isinstance(macro_properties['description'], str):
                    macro['description'] = macro_properties['description']
                else:
                    macro['description'] = ""

            elif isinstance(macro_properties, str):
                macro['value'] = macro_properties
                macro['type'] = str(0)
                macro['description'] = ""
        else:
            self.logger.error(f'Usermacro {macro_name} is not a valid usermacro name, skipping.')
            return False
        return macro
       
    def generate(self):
        macros=[]
        if "zabbix" in self.context and "usermacros" in self.context['zabbix']:
            for macro, properties in self.context['zabbix']['usermacros'].items():
                m = self.render_macro(macro, properties)
                pprint(m)
                if m:
                   macros.append(m)
        return macros
