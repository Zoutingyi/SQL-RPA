from abc import ABC, abstractmethod


class DatabaseConnector(ABC):
    """Abstract base for target database connectors."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the target database."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the database connection."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the connection is alive."""
        ...

    @abstractmethod
    async def get_tables(self) -> list[str]:
        """Return list of table names in the database."""
        ...

    @abstractmethod
    async def get_schema(self, table_name: str) -> dict:
        """Return schema info for a table.

        Returns: {"table_name": str, "columns": [{"name", "type", "nullable", "key", "default"}]}
        """
        ...

    @abstractmethod
    async def query(self, sql: str, params: dict | None = None) -> list[dict]:
        """Execute a SELECT query and return rows as list of dicts."""
        ...

    @abstractmethod
    async def execute(self, sql: str, params: dict | None = None) -> int:
        """Execute a write statement (INSERT/UPDATE/DELETE). Returns affected row count."""
        ...

    @abstractmethod
    async def execute_many(self, sql: str, rows: list[tuple]) -> int:
        """Execute a parameterized multi-row write. Returns affected row count."""
        ...

    @abstractmethod
    def quote_identifier(self, identifier: str) -> str:
        """Return a dialect-quoted identifier after it has been trusted/validated."""
        ...

    @abstractmethod
    def placeholder(self) -> str:
        """Return the parameter placeholder for this dialect."""
        ...

    @abstractmethod
    def transaction(self):
        """Return an async context manager for an atomic transaction.

        The yielded object provides query(sql, params), execute(sql, params), and
        execute_many(sql, rows).
        """
        ...
