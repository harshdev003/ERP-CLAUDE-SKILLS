# Creating a page with an inline database

Use this when the content is a register, log or inventory whose entries share fields, and it belongs
on a page as an inline database rather than as a table block. `Guide/Placement Rules` §6 reserves
direct REST writes for exactly this case, because `create_page` cannot create a database. The write
then carries the bookkeeping the skills would otherwise do (last section below).

## Order of writes

A database created with `POST /databases` is appended at the **end** of its parent page. Build the
page in three writes so `# Related` and `# Sources` still close it:

1. `notion.create_page(parent, title, blocks)` with the body **up to and including** the heading the
   database sits under. Leave `Related` and `Sources` out of this call.
2. Create the database under the new page (`Notion-Version: 2025-09-03`):

   ```python
   import json
   db = notion._request("POST", "/databases", data=json.dumps({
       "parent": {"type": "page_id", "page_id": page["id"]},
       "title": [{"type": "text", "text": {"content": "<Database title>"}}],
       "is_inline": True,
       "initial_data_source": {"properties": {
           "<Title column>": {"title": {}},
           "<Number column>": {"number": {"format": "number"}},
           "<Select column>": {"select": {"options": [{"name": "<option>", "color": "green"}]}},
           "<Text column>": {"rich_text": {}},
           "<Date column>": {"date": {}},
       }},
   }))
   ds_id = db["data_sources"][0]["id"]   # rows are created against the data source, not the database
   ```

3. `notion.append_block_children(page["id"], [...])` with the `Related` and `Sources` blocks, so they
   land after the database.

Rows: `notion.create_database_row(ds_id, {"title": make_title(...), "<Select column>": make_select(...), ...})`.
Key the title as `"title"` on create, per the title-by-ID rule in `notion_rest.py`.

## Changing the schema later

`PATCH /data_sources/<ds_id>` with a `properties` object:

- `"<Column>": None` deletes the column and its values.
- `"<Column>": {"name": "<New name>"}` renames it; existing values are kept.
- `"<Select column>": {"select": {"options": [...]}}` sets the option list. List every option the
  column should keep: values on existing rows match options by name.

Rename the database itself with `PATCH /databases/<db_id>` and a new `title`. Remove a row with
`PATCH /pages/<row_id>` and `{"in_trash": true}`; it stays recoverable from Notion's trash.

## Bookkeeping the direct write must do by hand

- **Guide/Index line.** Insert it in alphabetical order within its section. Find the block it
  follows, then `PATCH /blocks/<Guide/Index page id>/children` with `{"children": [line], "after":
  "<that block id>"}`. Format: the backticked path, linked to the page, then ` — ` and a one-line
  description, matching the neighbouring lines.
- **Cache invalidation** as in Step 6b, for the new page and any page whose text changed.
- **`# Sources`** at the bottom of the page, written in step 3 above.
- **Icon.** A subpage of a tool page takes an emoji icon (see SKILL.md → `Tools/` pages).
