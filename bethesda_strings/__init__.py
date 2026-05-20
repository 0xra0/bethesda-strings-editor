"""
Python library for reading and editing Bethesda Skyrim string files.
Supports .strings, .dlstrings, and .ilstrings formats.
"""

from .core import BethesdaStringFile, StringDataObject
from .operations import FilterFunction, ModificationFunction
from .encoding import EncodingConverter
from .xml_handler import XMLHandler

__version__ = "0.1.0"
__all__ = [
    "BethesdaStringFile",
    "StringDataObject",
    "FilterFunction",
    "ModificationFunction",
    "EncodingConverter",
    "XMLHandler"
]
