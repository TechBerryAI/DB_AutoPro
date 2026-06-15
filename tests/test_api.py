"""API endpoint tests."""

def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Database Automation Pro" in response.json()["app"]


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_success(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_login_failure(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


def test_protected_endpoint_without_auth(client):
    response = client.get("/api/v1/monitoring/dashboard")
    assert response.status_code == 401


def test_dashboard_with_auth(client, auth_headers):
    response = client.get("/api/v1/monitoring/dashboard", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "total_backups" in data


def test_list_backups_with_auth(client, auth_headers):
    response = client.get("/api/v1/backups/", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_rbac_viewer_cannot_create_backup(client):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "viewer123"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/v1/backups/",
        json={"db_type": "postgresql", "method": "pg_dump"},
        headers=headers,
    )
    assert response.status_code == 403
