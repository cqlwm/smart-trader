class PersistenceError(Exception):
    """Base exception for persistence module."""
    pass

class DatabaseConnectionError(PersistenceError):
    """Raised when the database connection fails."""
    pass

class DatabaseQueryError(PersistenceError):
    """Raised when a database query fails."""
    pass

class MigrationError(PersistenceError):
    """Raised when a data migration fails."""
    pass
