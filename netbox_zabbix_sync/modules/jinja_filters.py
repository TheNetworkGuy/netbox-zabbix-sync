"""Custom Jinja2 filters, these will be loaded by the Jinja2 parsers."""

import re

import dateutil
import jsonpath


def dict_keys(data, keys=None):
    """
    Filters specific keys from a list of dictionaries.
    """
    new_list = []
    if not keys:
        keys = []
    # If we only filter for one key, convert to list
    elif keys and isinstance(keys, str):
        keys = [keys]
    # Input data should be a list
    if isinstance(data, list):
        for d in data:
            new_dict = {}
            # If we find a dict, transfer the
            # needed keys to our new list
            if isinstance(d, dict):
                for k in keys:
                    v = d.get(k, None)
                    if v:
                        new_dict[k] = v
            if new_dict:
                new_list.append(new_dict)
    if new_list:
        return new_list
    return ""  # noop


def json_path(data, path):
    """
    JSONPath filter.
    See https://jg-rp.github.io/python-jsonpath/syntax/ for syntax.
    """
    return jsonpath.findall(path, data)


def regex_replace(s, find, replace):
    """
    Simple Regex replace
    """
    return re.sub(find, replace, s)


def regex_search(s, find):
    """
    Regex search
    Only returns matches, empty string otherwise.
    """
    match = re.search(find, s)
    if match:
        return match.group()
    return ""


def strftime(date, fmt=None):
    """
    Format timestrings
    See https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
    for formatting.
    """
    date = dateutil.parser.parse(date)
    native = date.replace(tzinfo=None)
    format = fmt or "%Y-%m-%d %H:%M:%S"
    return native.strftime(format)
