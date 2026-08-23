"""
SharePoint List Migration Utility

A standalone utility to read records from one SharePoint list and move
selected records to another SharePoint list with filtering support.
"""

import argparse
import sys

from office365.runtime.auth.user_cred import UserCredential
from office365.sharepoint.client_context import ClientContext


def fetch_list_records(site_url, list_title, filter_query=None, select_fields=None):
    """
    Fetch records from a SharePoint list using OData queries.

    Args:
        site_url: SharePoint site URL (e.g., https://contoso.sharepoint.com/sites/site)
        list_title: Title of the SharePoint list
        filter_query: OData filter query (e.g., "Status eq 'Pending'")
        select_fields: List of field names to select

    Returns:
        List of dictionaries representing list items
    """
    ctx = ClientContext(site_url)

    list_obj = ctx.web.lists.get_by_title(list_title)
    ctx.load(list_obj)
    ctx.execute_query()

    # Build OData query
    query = ""
    if select_fields:
        select_str = ",".join(select_fields)
        query = f"$select={select_str}"
        if filter_query:
            query += f"&$filter={filter_query}"
    elif filter_query:
        query = f"$filter={filter_query}"

    items = list_obj.get_items(query)
    ctx.load(items)
    ctx.execute_query()

    # Convert to list of dicts
    records = []
    for item in items:
        record = {}
        for field in item.properties:
            if select_fields and field not in select_fields:
                continue
            record[field] = item.properties[field]
        records.append(record)

    return records


def move_records_to_list(
    source_site_url,
    source_list_title,
    dest_site_url,
    dest_list_title,
    record_ids,
    filter_query=None,
):
    """
    Move records from source SharePoint list to destination SharePoint list.

    Args:
        source_site_url: Source SharePoint site URL
        source_list_title: Source list title
        dest_site_url: Destination SharePoint site URL
        dest_list_title: Destination list title
        record_ids: List of record IDs to move
        filter_query: Optional additional filter

    Returns:
        Number of records moved
    """
    # Fetch records from source
    ctx = ClientContext(source_site_url)
    source_list = ctx.web.lists.get_by_title(source_list_title)

    # Build query to get the records
    query = ""
    id_filter = ""
    if record_ids:
        id_filter = f" and ID in ({','.join(str(i) for i in record_ids)})"

    if filter_query:
        query = f"$filter={filter_query}{id_filter}"
    else:
        query = f"$filter=ID in ({','.join(str(i) for i in record_ids)})"

    items = source_list.get_items(query)
    ctx.load(items)
    ctx.execute_query()

    # Get destination list
    dest_ctx = ClientContext(dest_site_url)
    dest_list = dest_ctx.web.lists.get_by_title(dest_list_title)
    dest_ctx.load(dest_list)
    dest_ctx.execute_query()

    moved_count = 0

    for item in items:
        # Get all properties
        item_properties = item.properties

        # Copy to destination, skipping internal SharePoint fields
        copy_properties = {}
        for key, value in item_properties.items():
            if key in (
                "ID",
                "SystemCreatedAt",
                "SystemUpdatedAt",
                "FileLeafRef",
                "FileRef",
                "UniqueId",
                "Attachments",
            ):
                continue
            copy_properties[key] = value

        # Add to destination list
        new_item = dest_list.add_item(copy_properties)
        dest_ctx.execute_query()

        # Delete from source list
        item.delete_object()
        ctx.execute_query()

        moved_count += 1

    return moved_count


def main():
    parser = argparse.ArgumentParser(
        description="SharePoint List Migration Utility"
    )
    parser.add_argument(
        "--source-url",
        required=True,
        help="Source SharePoint site URL (e.g., https://contoso.sharepoint.com/sites/site)",
    )
    parser.add_argument(
        "--source-list",
        required=True,
        help="Source list title",
    )
    parser.add_argument(
        "--dest-url",
        required=True,
        help="Destination SharePoint site URL (e.g., https://contoso.sharepoint.com/sites/site)",
    )
    parser.add_argument(
        "--dest-list",
        required=True,
        help="Destination list title",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="OData filter query (e.g., Status eq 'Pending')",
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated list of fields to select (e.g., Title,ID,Status)",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move selected records to destination list",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview records without moving",
    )

    args = parser.parse_args()

    # Parse fields if provided
    select_fields = None
    if args.fields:
        select_fields = [f.strip() for f in args.fields.split(",")]

    # Fetch records from source
    print(f"Fetching records from: {args.source_url}/{args.source_list}")
    records = fetch_list_records(
        args.source_url,
        args.source_list,
        filter_query=args.filter,
        select_fields=select_fields,
    )

    if not records:
        print("No records found matching the criteria.")
        return

    print(f"Found {len(records)} records.")

    if args.preview:
        print("\n--- Preview of records ---")
        for i, record in enumerate(records, 1):
            print(f"\nRecord {i}:")
            for key, value in record.items():
                print(f"  {key}: {value}")
        return

    if not args.move:
        print("\nUse --move to move these records to the destination list.")
        print("Use --preview to see records without moving.")
        return

    # Ask for confirmation
    print(f"\n--- Moving {len(records)} records to {args.dest_url}/{args.dest_list} ---")
    confirm = input("Type 'yes' to confirm: ")
    if confirm.lower() != "yes":
        print("Operation cancelled.")
        return

    # Move records
    print("Moving records...")
    ids = [r.get("ID") for r in records if r.get("ID")]
    moved = move_records_to_list(
        args.source_url,
        args.source_list,
        args.dest_url,
        args.dest_list,
        ids,
        filter_query=args.filter,
    )

    print(f"Successfully moved {moved} records to the destination list.")


if __name__ == "__main__":
    main()