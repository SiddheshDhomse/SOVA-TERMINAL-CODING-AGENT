"""
SharePoint List Migration Utility - Flask Application

A standalone utility to read records from one SharePoint list and move
selected records to another SharePoint list with filtering support.

Run with: python app.py
Then open http://127.0.0.1:8787 in your browser
"""

from flask import Flask, render_template, request, jsonify
from office365.runtime.auth.user_credential import UserCredential
from office365.sharepoint.client_context import ClientContext
import logging

app = Flask(__name__)
app.secret_key = 'sharepoint-migrator-secret'

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fetch_list_records(site_url, list_title, filter_query=None, select_fields=None):
    """
    Fetch records from a SharePoint list using OData queries.
    """
    try:
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

        logger.info(f"Fetched {len(records)} records from {list_title}")
        return records, None
    except Exception as e:
        logger.error(f"Error fetching records: {str(e)}")
        return None, str(e)


def move_records_to_list(
    source_site_url,
    source_list_title,
    dest_site_url,
    dest_list_title,
    record_ids,
    filter_query=None,
    dest_fields=None,
):
    """
    Move records from source SharePoint list to destination SharePoint list.
    """
    try:
        # Fetch records from source
        ctx = ClientContext(source_site_url)
        source_list = ctx.web.lists.get_by_title(source_list_title)

        # Build query to get the records
        id_filter = ""
        if record_ids:
            id_filter = f" and ID in ({','.join(str(i) for i in record_ids)})"

        query = ""
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
            skip_keys = {"ID", "SystemCreatedAt", "SystemUpdatedAt",
                        "FileLeafRef", "FileRef", "UniqueId", "Attachments"}
            for key, value in item_properties.items():
                if key not in skip_keys:
                    copy_properties[key] = value

            # Add to destination list
            new_item = dest_list.add_item(copy_properties)
            dest_ctx.execute_query()

            # Delete from source list
            item.delete_object()
            ctx.execute_query()

            moved_count += 1

        message = f"Successfully moved {moved_count} records"
        logger.info(message)
        return moved_count, None
    except Exception as e:
        logger.error(f"Error moving records: {str(e)}")
        return 0, str(e)


@app.route('/')
def index():
    """Serve the main HTML page."""
    return render_template('sharepoint_ui.html')


@app.route('/api/fetch-records', methods=['POST'])
def api_fetch_records():
    """API endpoint to fetch records from SharePoint list."""
    data = request.get_json()

    site_url = data.get('siteUrl', '').strip()
    list_title = data.get('listTitle', '').strip()
    filter_query = data.get('filterQuery', None)
    select_fields = data.get('selectFields', None)

    if not site_url or not list_title:
        return jsonify({'error': 'Site URL and List Title are required'}), 400

    records, error = fetch_list_records(site_url, list_title, filter_query, select_fields)

    if error:
        return jsonify({'error': error}), 500

    return jsonify({
        'success': True,
        'records': records,
        'count': len(records)
    })


@app.route('/api/move-records', methods=['POST'])
def api_move_records():
    """API endpoint to move records to destination SharePoint list."""
    data = request.get_json()

    source_site_url = data.get('sourceSiteUrl', '').strip()
    source_list_title = data.get('sourceListTitle', '').strip()
    dest_site_url = data.get('destSiteUrl', '').strip()
    dest_list_title = data.get('destListTitle', '').strip()
    selected_record_ids = data.get('selectedRecordIds', [])
    dest_fields = data.get('destFields', None)

    if not all([source_site_url, source_list_title, dest_site_url, dest_list_title]):
        return jsonify({'error': 'All URL and list title fields are required'}), 400

    if not selected_record_ids:
        return jsonify({'error': 'No records selected'}), 400

    moved_count, error = move_records_to_list(
        source_site_url,
        source_list_title,
        dest_site_url,
        dest_list_title,
        selected_record_ids,
        filter_query=data.get('filterQuery'),
        dest_fields=dest_fields
    )

    if error:
        return jsonify({'error': error}), 500

    return jsonify({
        'success': True,
        'moved': moved_count,
        'message': f'Successfully moved {moved_count} records to {dest_list_title}'
    })


if __name__ == '__main__':
    print("=" * 60)
    print("SharePoint List Migration Utility")
    print("=" * 60)
    print(f"Starting server at http://127.0.0.1:8787")
    print("Open your browser and navigate to the URL above")
    print("=" * 60)
    print()
    print("Features:")
    print("  • Read records from SharePoint list with OData filtering")
    print("  • Select specific fields to display")
    print("  • Move selected records to destination list")
    print("  • Preview records before moving")
    print()
    print("Press Ctrl+C to stop the server")
    print()
    app.run(host='127.0.0.1', port=8787, debug=False)