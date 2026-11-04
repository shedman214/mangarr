"""English Toomics public/free episodes. Never unlock VIP or coin episodes."""
import re

from ._html_archive import HtmlArchive
from .base import SourceSeries, SourceChapter

SERIES = r"/en/webtoon/episode/toon/([0-9]+)"
CHAPTER = r"(/en/webtoon/detail/code/[0-9]+/ep/[0-9]+/toon/[0-9]+)"


class ToomicsSource(HtmlArchive):
    name = "toomics"
    base_url = "https://toomics.com"
    chapter_pattern = CHAPTER
    session_cookie = ""

    def configure(self, session_cookie=""):
        if "\r" in session_cookie or "\n" in session_cookie or len(session_cookie) > 16384:
            raise ValueError("Invalid Toomics session cookie")
        self.session_cookie = session_cookie.strip()

    async def request(self, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if self.session_cookie:
            headers["Cookie"] = self.session_cookie
        return await super().request(path, headers=headers, **kwargs)

    async def search_series(self, query):
        if not query.strip():
            return []
        soup = await self.html("/en/webtoon/search_v2", params={"keyword": query.strip()})
        results = {}
        for anchor in soup.select("a"):
            href = str(anchor.get("href", ""))
            match = re.search(r"(/en/webtoon/episode/toon/[0-9]+)", str(anchor.get("onclick", "")))
            identifier = self.identifier(match.group(1) if match else href, SERIES)
            title = anchor.get_text(" ", strip=True)
            image = anchor.select_one("img[alt]")
            if not title and image:
                title = str(image.get("alt", ""))
            if identifier and title:
                results[identifier] = SourceSeries(self.name, identifier, title, self.base_url + "/en/webtoon/episode/toon/" + identifier)
        return list(results.values())[:30]

    async def test_session(self):
        """Read-only check; a reachable public page alone does not prove login."""
        soup = await self.html("/en", follow_redirects=False, timeout=20, retries=0)
        for anchor in soup.select("a[href], a[onclick], button[onclick]"):
            action = str(anchor.get("href", "")) + " " + str(anchor.get("onclick", ""))
            if re.search(r"(?:/|\b)(?:logout|signout|sign_out)\b", action, re.I):
                return {"status": "signed_in", "message": "Cookie accepted: Toomics shows a signed-in session. Family Safe and free-episode availability still depend on your Toomics account."}
        if soup.select_one('[onclick*="modal-login-header"]'):
            return {"status": "signed_out", "message": "Toomics shows a signed-out session. The cookie may have expired or be incomplete. Sign in on Toomics and copy a fresh cookie."}
        return {"status": "unknown", "message": "Toomics responded, but Mangarr could not confirm sign-in. The site may have changed or returned a check page. This does not confirm that the cookie works."}

    async def list_chapters(self, external_id):
        if not external_id.isdigit():
            raise ValueError("Invalid Toomics series ID")
        soup = await self.html("/en/webtoon/episode/toon/" + external_id)
        chapters = {}
        for anchor in soup.select("a.js-episode-link, .list-ep a"):
            # Require the site's explicit FREE marker; do not treat a login or
            # purchase popup as an available chapter.
            if not re.search(r"\bFREE\b", anchor.get_text(" ", strip=True), re.I):
                continue
            match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", str(anchor.get("onclick", "")))
            identifier = self.identifier(match.group(1) if match else str(anchor.get("href", "")), CHAPTER)
            if not identifier or not identifier.endswith("/toon/" + external_id):
                continue
            number = float(re.search(r"/ep/([0-9]+)/", identifier).group(1))
            title = anchor.select_one(".cell-title")
            chapters[identifier] = SourceChapter(self.name, identifier, number, title=title.get_text(" ", strip=True) if title else "", url=self.base_url + identifier)
        if not chapters:
            raise RuntimeError("Toomics returned no free readable episodes. Check the series link, session and Family Safe setting; VIP/coin episodes are not supported.")
        return sorted(chapters.values(), key=lambda c: c.number)

    async def get_pages(self, chapter_external_id):
        if not re.fullmatch(CHAPTER, chapter_external_id):
            raise ValueError("Invalid Toomics chapter ID")
        soup = await self.html(chapter_external_id)
        return self.page_urls([str(i.get("data-src") or i.get("data-original") or i.get("srcset") or i.get("src") or "") for i in soup.select("#viewer-img source, #viewer-img img")], chapter_external_id)


source = ToomicsSource()
