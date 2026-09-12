import unittest

from fastapi.testclient import TestClient
from server.app import app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = self.enterContext(TestClient(app))

    def test_root(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"msg": "首页"})

    def test_user_routes(self) -> None:
        response = self.client.get("/users/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"msg": "用户列表"})
        response = self.client.get("/users/42")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"user_id": 42})

    def test_invalid_user_id(self) -> None:
        self.assertEqual(self.client.get("/users/not-an-integer").status_code, 422)

    def test_api_documentation(self) -> None:
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(
            set(schema["paths"]), {"/", "/users/", "/users/{user_id}", "/segmentations"}
        )


if __name__ == "__main__":
    unittest.main()
