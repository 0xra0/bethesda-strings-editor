"""
Helper functions and utilities for string operations.
"""

import struct
from typing import Callable, Optional
from .core import StringDataObject


# Type aliases for clarity
FilterFunction = Callable[[StringDataObject], bool]
ModificationFunction = Callable[[bytearray, Optional[StringDataObject]], bytearray]


def create_length_filter(min_length: int, max_length: Optional[int] = None) -> FilterFunction:
    """Create a filter function based on string length."""
    def filter_func(s: StringDataObject) -> bool:
        if max_length is not None:
            return min_length <= s.length <= max_length
        return s.length >= min_length
    return filter_func


def create_prefix_filter(prefix: str, encoding: str = 'utf-8') -> FilterFunction:
    """Create a filter function that matches strings starting with a prefix."""
    prefix_bytes = prefix.encode(encoding)
    
    def filter_func(s: StringDataObject) -> bool:
        data = s.string_array
        # Skip length prefix for dlstrings/ilstrings
        start_idx = 4 if s.has_length_prefix else 0
        if len(data) < start_idx + len(prefix_bytes):
            return False
        return data[start_idx:start_idx + len(prefix_bytes)] == prefix_bytes
    return filter_func


def create_replacement_modification(new_text: str, encoding: str = 'utf-8') -> ModificationFunction:
    """Create a modification function that replaces string content."""
    def mod_func(string_array: bytearray, s: Optional[StringDataObject]) -> bytearray:
        encoded = new_text.encode(encoding) + b'\x00'
        if s and s.has_length_prefix:
            # Include 4-byte length prefix
            length = len(encoded)
            return bytearray(struct.pack('<I', length) + encoded)
        return bytearray(encoded)
    return mod_func


def create_case_transform_modification(transform: str = 'upper') -> ModificationFunction:
    """Create a modification function that transforms string case."""
    def mod_func(string_array: bytearray, s: Optional[StringDataObject]) -> bytearray:
        # Extract text (skip length prefix if present, remove null terminator)
        start = 4 if (s and s.has_length_prefix) else 0
        end = -1 if string_array and string_array[-1] == 0 else None
        text_bytes = bytes(string_array[start:end])
        
        # Decode, transform, re-encode
        try:
            text = text_bytes.decode('utf-8')
        except UnicodeDecodeError:
            text = text_bytes.decode('windows-1252', errors='replace')
        
        if transform == 'upper':
            text = text.upper()
        elif transform == 'lower':
            text = text.lower()
        elif transform == 'title':
            text = text.title()
        
        encoded = text.encode('utf-8') + b'\x00'
        if s and s.has_length_prefix:
            length = len(encoded)
            return bytearray(struct.pack('<I', length) + encoded)
        return bytearray(encoded)
    return mod_func
