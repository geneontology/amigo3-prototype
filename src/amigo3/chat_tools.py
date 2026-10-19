"""Read-only, build-pinned DuckDB tools, served to ACP agents over MCP stdio."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

import duckdb

MAX_RESULT_BYTES = 100_000


class DuckTools:
    def __init__(self, build_path: Path, timeout: float = 15):
        self.path = build_path.resolve()
        self.timeout = timeout
        if not (self.path / "integration.duckdb").is_file():
            raise ValueError("No integration.duckdb in the selected build")

    def query(self, sql: str, limit: int = 100) -> dict:
        """Run one SELECT, with bounded results and no external file/network access."""
        if not sql.strip() or len(sql) > 20_000 or not 1 <= limit <= 500:
            raise ValueError("Provide SQL (up to 20,000 characters) and a limit from 1 to 500")
        # A private in-memory connection avoids changing the browser's DuckDB configuration.
        with duckdb.connect(config={"memory_limit": "256MB", "threads": "2"}) as db:
            path = str(self.path / "integration.duckdb").replace("'", "''")
            db.execute(f"ATTACH '{path}' AS snapshot (READ_ONLY)")
            db.execute("USE snapshot")
            db.execute("SET enable_external_access=false")
            db.execute("SET lock_configuration=true")
            statements = db.extract_statements(sql)
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                raise ValueError("Only a single SELECT (including WITH) is allowed")
            timer = threading.Timer(self.timeout, db.interrupt)
            timer.start()
            try:
                cursor = db.execute(sql)
                columns = [{"name": c[0], "type": str(c[1])} for c in cursor.description]
                rows = cursor.fetchmany(limit + 1)
                truncated = len(rows) > limit
                result_rows, size = [], 0
                for row in rows[:limit]:
                    encoded = json.dumps(row, default=str, ensure_ascii=False)
                    size += len(encoded.encode("utf-8"))
                    if size > MAX_RESULT_BYTES:
                        truncated = True
                        break
                    result_rows.append(json.loads(encoded))
                return {
                    "build_id": self.path.name,
                    "columns": columns,
                    "rows": result_rows,
                    "returned_rows": len(result_rows),
                    "truncated": truncated,
                    "sql": sql,
                }
            finally:
                timer.cancel()
                timer.join()

    def schema(self, table: str | None = None) -> dict:
        """Discover tables/columns before writing SQL; identifiers are never inferred."""
        if table is None:
            return self.query(
                "SELECT table_name, count(*) AS column_count FROM information_schema.columns "
                "WHERE table_catalog = 'snapshot' GROUP BY table_name ORDER BY table_name",
                limit=500,
            )
        where = "table_catalog = 'snapshot'"
        where += " AND table_name = '" + table.replace("'", "''") + "'"
        return self.query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE "
            + where
            + " ORDER BY table_name, ordinal_position",
            limit=500,
        )

    def manifest(self) -> dict:
        return json.loads((self.path / "manifest.json").read_text())


def create_server(build_path: Path):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    tools = DuckTools(build_path)
    server = FastMCP(
        "amigo3-duckdb",
        instructions=(
            "Query the immutable AmiGO build using database_schema, query_sql and build_manifest. "
            "Inspect schema first. Results are capped; use SQL aggregation for counts and explicit "
            "ORDER BY/LIMIT/OFFSET for paging. Cite identifiers and build provenance. Keep NOT annotations, "
            "isoforms, evidence codes, and review context distinct; reviews do not replace annotations."
            " Before linking query results to UI tools such as enrichment, read ui_skill('ui-links')."
        ),
    )

    read_only = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    @server.tool(annotations=read_only)
    def ui_skill(name: str = "ui-links") -> str:
        """Read a UI integration skill: ui-links explains enrichment, record and search URLs."""
        if name != "ui-links":
            raise ValueError("Unknown skill; available: ui-links")
        return (Path(__file__).parent / "skills" / "ui-links.md").read_text()

    @server.tool(annotations=read_only)
    def database_schema(table: str | None = None) -> dict:
        """List snapshot table names; pass a table name to discover its columns and types."""
        return tools.schema(table)

    @server.tool(annotations=read_only)
    def query_sql(sql: str, limit: int = 100) -> dict:
        """Execute one read-only SELECT on the pinned build (15s, max 500 rows/100KB).

        Returns columns and row arrays, build ID, SQL, and an explicit truncation flag.
        Files, URLs, extensions, writes, and multiple statements are unavailable.
        """
        return tools.query(sql, limit)

    @server.tool(annotations=read_only)
    def build_manifest() -> dict:
        """Get the pinned build's counts and input provenance, including source checksums."""
        return tools.manifest()

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    create_server(args.build).run(transport="stdio")


if __name__ == "__main__":
    main()
