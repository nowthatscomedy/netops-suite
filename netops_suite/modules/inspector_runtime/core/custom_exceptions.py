"""
Custom exception classes for the Network Inspector application.
"""

class NetworkInspectorError(Exception):
    """Base exception class for the application."""
    pass

class FileReadError(NetworkInspectorError):
    """Raised when there's an error reading a file."""
    pass

class ValidationError(NetworkInspectorError):
    """Raised for data validation errors."""
    pass

class DeviceConnectionError(NetworkInspectorError):
    """Raised for device connection failures."""
    pass 