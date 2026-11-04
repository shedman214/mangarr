"""The app page must be revalidated after updates; hashed assets may be cached."""
import httpx
from fastapi import FastAPI

from mangarr.main import mount_frontend


async def test_index_revalidates_and_assets_are_immutable(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "mangarr-icon.png").write_bytes(b"png")
    app = FastAPI()
    mount_frontend(app, tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for path in ("/", "/settings", "/series/12"):
            r = await c.get(path)
            assert r.status_code == 200 and r.text == "<html>app</html>"
            assert r.headers["cache-control"] == "no-cache"
        icon = await c.get("/mangarr-icon.png")
        assert icon.content == b"png" and icon.headers["cache-control"] == "no-cache"
        asset = await c.get("/assets/index-abc123.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        missing = await c.get("/assets/nope.js")
        assert missing.status_code == 404 and "immutable" not in missing.headers.get("cache-control", "")
        escape = await c.get("/..%2f..%2fetc%2fpasswd")
        assert escape.text == "<html>app</html>"  # falls back to the app, never leaves the folder
