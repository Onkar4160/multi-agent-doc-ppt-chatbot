"""Automated tests for /health, /auth/login, /files/upload, and /files endpoints using FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(name="client")
def client_fixture():
    """TestClient fixture that triggers app lifespan (creating DB tables)."""
    with TestClient(app) as client:
        yield client


def test_health_endpoint(client: TestClient):
    """Verify GET /health returns 200 OK and status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_login_success(client: TestClient):
    """Verify POST /auth/login returns JWT for demo credentials."""
    response = client.post(
        "/auth/login",
        json={"username": "demo", "password": "demo123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"


def test_auth_login_failure(client: TestClient):
    """Verify POST /auth/login returns 401 for bad credentials."""
    response = client.post(
        "/auth/login",
        json={"username": "demo", "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_file_upload_unauthorized(client: TestClient):
    """Verify POST /files/upload requires JWT token."""
    response = client.post(
        "/files/upload",
        files={"file": ("test.docx", b"dummy content", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 401


def test_file_upload_and_list_success(client: TestClient):
    """Verify file upload with JWT authentication and file listing."""
    # 1. Login to get token
    login_res = client.post(
        "/auth/login",
        json={"username": "demo", "password": "demo123"},
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Upload a valid docx file
    file_bytes = b"Hello docx test content"
    upload_res = client.post(
        "/files/upload",
        headers=headers,
        files={"file": ("sample_template.docx", file_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert upload_res.status_code == 201
    file_data = upload_res.json()
    assert file_data["filename"] == "sample_template.docx"
    assert file_data["file_type"] == "docx"
    assert "id" in file_data

    # 3. List files
    list_res = client.get("/files", headers=headers)
    assert list_res.status_code == 200
    files_list = list_res.json()
    assert len(files_list) >= 1
    assert any(f["filename"] == "sample_template.docx" for f in files_list)


def test_file_upload_invalid_extension(client: TestClient):
    """Verify uploading a disallowed file type returns 400 Bad Request."""
    login_res = client.post(
        "/auth/login",
        json={"username": "demo", "password": "demo123"},
    )
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    upload_res = client.post(
        "/files/upload",
        headers=headers,
        files={"file": ("malicious_script.exe", b"print('hello')", "application/x-msdownload")},
    )
    assert upload_res.status_code == 400
    assert "Unsupported file type" in upload_res.json()["detail"]
