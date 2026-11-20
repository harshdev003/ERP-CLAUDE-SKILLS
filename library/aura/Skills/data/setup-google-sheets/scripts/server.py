# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp[cli]>=2.0.0",
#   "google-api-python-client>=2.100",
#   "google-auth>=2.30",
#   "google-auth-oauthlib>=1.2",
# ]
# ///
"""Google Sheets MCP server (Sheets API v4), supporting three auth models.

  1. PER-USER OAUTH (default)  TOKEN_PATH points at a token minted by authorize.py.
     The agent acts as that human and sees exactly the sheets they can see. No admin
     needed; edits are attributed per-person.
  2. SHARE-MODE                SERVICE_ACCOUNT_PATH only. The service account is its
     own identity and sees only sheets explicitly shared with it.
  3. DWD (impersonation)       SERVICE_ACCOUNT_PATH + IMPERSONATE_SUBJECT. Requires a
     Workspace super-admin to authorize the service account's client ID.

Auth resolution order: TOKEN_PATH, then SERVICE_ACCOUNT_PATH.

Environment:
  TOKEN_PATH             OAuth token JSON written by authorize.py       (model 1)
                         Self-contained: embeds the client id/secret alongside the
                         refresh token, so nothing else is required.
  SERVICE_ACCOUNT_PATH   service-account key JSON                   (models 2 and 3)
  IMPERSONATE_SUBJECT    email of the user to impersonate               (model 3)

The MCP SDK 2.0 entry point is `MCPServer` (`FastMCP` belongs to the 1.x line), which
is why the dependency above is pinned to >=2.0.0.

Run: uv run server.py
"""
import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from mcp.server.mcpserver import MCPServer

# Deliberately narrow: spreadsheet read/write plus the ability to FIND sheets by name.
# drive.metadata.readonly cannot read file contents and avoids the "restricted"
# full-drive scope class.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

mcp = MCPServer("google-sheets")

# Clients are built lazily and cached so the server starts even before credentials
# exist, and reports a useful error per-call instead of dying at import.
_clients: dict = {}


def _ok(payload) -> str:
    return json.dumps(payload, default=str)


def _quote_tab(sheet_name: str) -> str:
    """Quote a tab title for A1 notation. An embedded apostrophe is doubled."""
    return "'{}'".format(sheet_name.replace("'", "''"))


def _unquote_tab(quoted: str) -> str:
    """Inverse of _quote_tab, for reading a tab title back out of a returned range."""
    if quoted.startswith("'") and quoted.endswith("'") and len(quoted) >= 2:
        return quoted[1:-1].replace("''", "'")
    return quoted


def _a1(sheet_name: str, cell_range: str = "") -> str:
    q = _quote_tab(sheet_name)
    return f"{q}!{cell_range}" if cell_range else q


def _err(exc: Exception) -> str:
    return json.dumps({"error": type(exc).__name__, "message": str(exc)})


def _user_oauth_creds(token_path: str):
    """Model 1: load the stored token, refreshing it when stale."""
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        try:  # persist the rotated access token so the next start is fast
            Path(token_path).write_text(creds.to_json(), encoding="utf-8")
        except OSError:
            pass
        return creds
    raise RuntimeError(
        f"OAuth token at {token_path} is invalid and cannot be refreshed. "
        "Re-run: uv run authorize.py <oauth_client.json>"
    )


def _credentials():
    token_path = os.environ.get("TOKEN_PATH")
    if token_path and Path(token_path).is_file():
        return _user_oauth_creds(token_path)

    sa_path = os.environ.get("SERVICE_ACCOUNT_PATH")
    if sa_path:
        creds = service_account.Credentials.from_service_account_file(sa_path, scopes=SCOPES)
        subject = os.environ.get("IMPERSONATE_SUBJECT")
        if subject:
            creds = creds.with_subject(subject)  # domain-wide delegation
        return creds

    raise RuntimeError(
        "No credentials configured. Set TOKEN_PATH (per-user OAuth: run authorize.py "
        "first) or SERVICE_ACCOUNT_PATH (service account)."
    )


def _svc(api: str):
    if api not in _clients:
        version = "v4" if api == "sheets" else "v3"
        _clients[api] = build(api, version, credentials=_credentials(), cache_discovery=False)
    return _clients[api]


def _sheets():
    return _svc("sheets").spreadsheets()


def _drive():
    return _svc("drive")


# --------------------------------------------------------------------------- reads


@mcp.tool()
def whoami() -> str:
    """Report the auth model and the identity in use. Call this first to verify setup."""
    try:
        token_path = os.environ.get("TOKEN_PATH")
        if token_path and Path(token_path).is_file():
            about = _drive().about().get(fields="user(emailAddress,displayName)").execute()
            return _ok({"model": "per-user OAuth", "identity": about.get("user", {}), "scopes": SCOPES})
        sa_path = os.environ.get("SERVICE_ACCOUNT_PATH")
        if sa_path:
            email = json.loads(Path(sa_path).read_text(encoding="utf-8")).get("client_email")
            subject = os.environ.get("IMPERSONATE_SUBJECT")
            return _ok({
                "model": "DWD impersonation" if subject else "share-mode (service account)",
                "serviceAccount": email,
                "impersonating": subject,
                "scopes": SCOPES,
            })
        return _ok({"model": "NONE - no credentials configured"})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def search_spreadsheets(name_contains: str = "", limit: int = 20) -> str:
    """Find Google Sheets by name across everything the authenticated identity can see.

    Covers My Drive, files shared with the identity, AND Shared Drives — in a Workspace
    org most team sheets live on a Shared Drive, which Drive omits unless asked for.
    """
    try:
        query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        if name_contains:
            # Backslash first, or escaping the quote would then re-escape its own backslash.
            escaped = name_contains.replace("\\", "\\\\").replace("'", "\\'")
            query += f" and name contains '{escaped}'"
        res = _drive().files().list(
            q=query, pageSize=limit,
            fields="files(id,name,modifiedTime,driveId,owners(emailAddress))",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
            corpora="allDrives",
        ).execute()
        return _ok({"files": res.get("files", [])})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def get_spreadsheet_info(spreadsheet_id: str) -> str:
    """Discover a spreadsheet: title plus every tab's name, numeric gid, size, and row-1 values.

    Always call this before a write, and build a {tab_name: gid} lookup from the result.
    API-created tabs get arbitrary gids, so never assume the first tab is gid 0.
    Row-1 values are a hint only: real headers often sit lower, so sample-read to confirm.
    """
    try:
        meta = _sheets().get(
            spreadsheetId=spreadsheet_id,
            fields=(
                "spreadsheetId,properties.title,"
                "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"
            ),
        ).execute()
        tabs = [s["properties"] for s in meta.get("sheets", [])]
        headers_by_tab: dict = {}
        if tabs:
            batch = _sheets().values().batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=[_a1(t["title"], "1:1") for t in tabs],
            ).execute()
            for vr in batch.get("valueRanges", []):
                rng = vr.get("range", "")
                title = _unquote_tab(rng.rsplit("!", 1)[0]) if "!" in rng else rng
                vals = vr.get("values") or [[]]
                headers_by_tab[title] = vals[0] if vals else []
        return _ok({
            "spreadsheetId": meta["spreadsheetId"],
            "title": meta["properties"]["title"],
            "worksheets": [{
                "sheetName": t["title"],
                "sheetId": t["sheetId"],
                "rowCount": t.get("gridProperties", {}).get("rowCount"),
                "columnCount": t.get("gridProperties", {}).get("columnCount"),
                "row1": headers_by_tab.get(t["title"], []),
            } for t in tabs],
        })
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def read_rows(spreadsheet_id: str, sheet_name: str, cell_range: str = "", has_headers: bool = True) -> str:
    """Read a range, e.g. cell_range="A1:Z50". Returns {headers, rows, rowCount}.

    Rows are dicts keyed by the first row of the range when has_headers is true, else
    flat arrays. When real headers sit below row 1, start the range at that row.
    """
    try:
        a1 = _a1(sheet_name, cell_range)
        values = _sheets().values().get(spreadsheetId=spreadsheet_id, range=a1).execute().get("values", [])
        if has_headers and values:
            headers, body = values[0], values[1:]
            rows = [
                {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
                for r in body
            ]
            return _ok({"headers": headers, "rows": rows, "rowCount": len(rows)})
        return _ok({"headers": [], "rows": values, "rowCount": len(values)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def read_cell_formats(spreadsheet_id: str, sheet_name: str, cell_range: str) -> str:
    """Read each cell's text and background colour for a range.

    The values API carries no formatting, so colour-coded sheets (status by fill colour)
    need this. Returns per row a list of {v: text, bg: [r, g, b]} with components
    normalised to 0-1. Within a colour the API omits zero components, so those default
    to 0 — but a cell reporting NO backgroundColor at all is unformatted, and its bg is
    null rather than [0,0,0]: default fill is white, so zeroing it would report every
    plain cell as black and wreck legend-based classification.

    To classify by colour, read the sheet's own legend cells and compare against those
    exact values rather than assuming what a colour name means numerically.
    """
    try:
        res = _sheets().get(
            spreadsheetId=spreadsheet_id,
            includeGridData=True,
            ranges=[_a1(sheet_name, cell_range)],
            fields="sheets(data(rowData(values(formattedValue,effectiveFormat/backgroundColor))))",
        ).execute()
        row_data = res["sheets"][0]["data"][0].get("rowData", [])
        out = []
        for row in row_data:
            cells = []
            for cell in row.get("values", []):
                bg = (cell.get("effectiveFormat") or {}).get("backgroundColor")
                cells.append({
                    "v": cell.get("formattedValue", ""),
                    "bg": None if bg is None else [round(bg.get(c, 0), 3) for c in ("red", "green", "blue")],
                })
            out.append(cells)
        return _ok({"range": _a1(sheet_name, cell_range), "rows": out, "rowCount": len(out)})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# -------------------------------------------------------------------------- writes


@mcp.tool()
def add_rows(spreadsheet_id: str, sheet_name: str, rows_json: str,
             value_input_option: str = "USER_ENTERED", header_row: int = 1) -> str:
    """Append rows to a tab. rows_json is a JSON array of dicts (keyed by header) or flat arrays.

    Dict keys must match the sheet's header text exactly, including case and spaces.
    USER_ENTERED makes '=FORMULA' strings resolve as live formulas; RAW stores them as text.

    header_row is which row holds the real headers that dict keys map to columns by. It
    defaults to 1, but sheets routinely open with a legend or banner row instead — so
    sample-read first (get_spreadsheet_info / read_rows) and pass the row you actually
    saw. Aligning to the wrong row silently writes values into the wrong columns.
    Ignored for flat-array rows, which are positional.
    """
    try:
        rows = json.loads(rows_json)
        if rows and isinstance(rows[0], dict):
            if header_row < 1:
                raise ValueError(f"header_row must be >= 1, got {header_row}")
            hdr = _sheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=_a1(sheet_name, f"{header_row}:{header_row}"),
            ).execute().get("values") or [[]]
            headers = hdr[0] if hdr and hdr[0] else []
            # Falling back to dict key order here would make the guard below vacuous —
            # any key set would "match" itself and land in whatever order the JSON had.
            if not headers:
                raise ValueError(
                    f"row {header_row} of '{sheet_name}' is empty, so there is nothing to "
                    "map dict keys onto. Pass the header_row you saw in a sample read, "
                    "write the header row first with update_cells, or send flat arrays "
                    "to write by position."
                )
            unknown = sorted({k for r in rows for k in r} - set(headers))
            if unknown:
                raise ValueError(
                    f"keys not present in row {header_row} of '{sheet_name}': {unknown}. "
                    f"Headers found: {headers}. Sample-read the tab and pass the real "
                    "header_row, or match the header text exactly."
                )
            values = [[r.get(h, "") for h in headers] for r in rows]
        else:
            values = rows
        res = _sheets().values().append(
            spreadsheetId=spreadsheet_id, range=_a1(sheet_name),
            valueInputOption=value_input_option, insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        return _ok({"updates": res.get("updates", {})})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def update_cells(spreadsheet_id: str, range_a1: str, values_json: str,
                 value_input_option: str = "USER_ENTERED") -> str:
    """Overwrite an A1 range, e.g. "'Weekly Summary'!A1:G5", with a 2D array."""
    try:
        res = _sheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_a1,
            valueInputOption=value_input_option,
            body={"values": json.loads(values_json)},
        ).execute()
        return _ok({"updatedRange": res.get("updatedRange"), "updatedCells": res.get("updatedCells")})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def batch_update(spreadsheet_id: str, requests_json: str) -> str:
    """Run spreadsheets.batchUpdate with a JSON array of request objects.

    Covers addSheet, repeatCell, updateSheetProperties, addConditionalFormatRule,
    mergeCells, updateBorders, autoResizeDimensions and the rest. Applies atomically in
    order: if one request fails they all roll back. Returns replies, including the
    sheetId of any tab created.
    """
    try:
        res = _sheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": json.loads(requests_json)}
        ).execute()
        return _ok({"replies": res.get("replies", [])})
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@mcp.tool()
def create_spreadsheet(title: str) -> str:
    """Create a new spreadsheet and return its spreadsheetId."""
    try:
        res = _sheets().create(
            body={"properties": {"title": title}}, fields="spreadsheetId,properties.title"
        ).execute()
        return _ok(res)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


if __name__ == "__main__":
    # JSON-RPC over stdio is UTF-8; force it so non-ASCII sheet content survives on
    # Windows consoles, which default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mcp.run()
