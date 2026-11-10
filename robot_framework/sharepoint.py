"""This module contains functions to download and upload files on SharePoint."""

import os

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from office365.runtime.client_request_exception import ClientRequestException
from office365.sharepoint.client_context import ClientContext

SHAREPOINT_SMALL_UPLOAD_LIMIT = 4 * 1024 * 1024
SHAREPOINT_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024


def sharepoint_client(tenant: str, client_id: str, thumbprint: str, cert_path: str, sharepoint_site_url: str, orchestrator_connection: OrchestratorConnection) -> ClientContext:
    """Creates and returns a SharePoint client context."""
    cert_credentials = {
        "tenant": tenant,
        "client_id": client_id,
        "thumbprint": thumbprint,
        "cert_path": cert_path
    }
    ctx = ClientContext(sharepoint_site_url).with_client_certificate(**cert_credentials)

    # Load and verify connection
    web = ctx.web
    ctx.load(web)
    ctx.execute_query()

    orchestrator_connection.log_info(f"Authenticated successfully. Site Title: {web.properties['Title']}")
    return ctx


def download_file(client: ClientContext, sharepoint_file_url: str, download_path: str) -> bool:
    """Downloads a file from SharePoint to download_path.

    Returns:
        True if the file was downloaded, False if it doesn't exist on SharePoint.
    """
    try:
        with open(download_path, "wb") as local_file:
            client.web.get_file_by_server_relative_path(sharepoint_file_url).download(local_file).execute_query()
    except ClientRequestException as error:
        os.remove(download_path)
        if error.response is not None and error.response.status_code == 404:
            return False
        raise
    return True


def upload_file(client: ClientContext, folder_path: str, local_file_path: str, file_name: str, orchestrator_connection: OrchestratorConnection) -> None:
    """Uploads a local file to a SharePoint folder, overwriting any existing file with the same name.
    Files larger than 4 MB are uploaded in chunks.
    """
    target_folder = client.web.get_folder_by_server_relative_url(folder_path)
    client.load(target_folder)
    client.execute_query()

    file_size = os.path.getsize(local_file_path)

    if file_size <= SHAREPOINT_SMALL_UPLOAD_LIMIT:
        with open(local_file_path, "rb") as file_content:
            target_folder.files.add(file_name, file_content.read(), True).execute_query()
        return

    def log_upload_progress(uploaded_bytes):
        if uploaded_bytes:
            orchestrator_connection.log_trace(f"Uploaded {uploaded_bytes} of {file_size} bytes for '{file_name}'")

    with open(local_file_path, "rb") as file_content:
        target_folder.files.create_upload_session(
            file_content,
            SHAREPOINT_UPLOAD_CHUNK_SIZE,
            log_upload_progress,
            file_name=file_name,
        ).execute_query()
