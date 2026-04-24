"""Async WebArena task for CUGA's BrowserEnvGymAsync.

Uses BrowserGym's WebArenaInstance for URLs, credentials, and task configs.
Skips WebArena evaluator entirely — we compare answers externally.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import tempfile
from typing import Optional, Tuple

import playwright.async_api

from browsergym.webarena.instance import WebArenaInstance
from cuga.backend.browser_env.browser.open_ended_async import AbstractBrowserTask

logger = logging.getLogger(__name__)


class GenericWebArenaTask(AbstractBrowserTask):
    """Async WebArena task that uses BrowserGym's WebArenaInstance
    for correct URLs and credentials."""

    def __init__(self, seed: int, task_id: Optional[int] = None, **kwargs) -> None:
        super().__init__(seed)
        self.viewport = {"width": 1280, "height": 720}
        self.slow_mo = 1000
        self.timeout = 10000

        if task_id is None:
            raise ValueError("task_id is required")

        # Use BrowserGym's WebArenaInstance for URLs and credentials
        self.wa = WebArenaInstance()

        # Load task configs from webarena package
        import webarena
        all_configs_str = importlib.resources.files(webarena).joinpath("test.raw.json").read_text()

        # Substitute URL placeholders using WebArenaInstance's URLs
        for pattern, url_key in {
            "__GITLAB__": "gitlab",
            "__REDDIT__": "reddit",
            "__SHOPPING__": "shopping",
            "__SHOPPING_ADMIN__": "shopping_admin",
            "__WIKIPEDIA__": "wikipedia",
            "__MAP__": "map",
        }.items():
            all_configs_str = all_configs_str.replace(pattern, self.wa.urls[url_key])

        all_configs = json.loads(all_configs_str)
        task_configs = [c for c in all_configs if c["task_id"] == task_id]
        if not task_configs:
            raise ValueError(f"No task config for task_id={task_id}")

        self.task_configs = task_configs
        self.config = None
        self.config_file = None

    @classmethod
    def get_task_id(cls):
        raise NotImplementedError

    async def setup(self, page: playwright.async_api.Page) -> Tuple[str, dict]:
        """Login to required sites and navigate to start URL."""
        self.config = self.random.choice(self.task_configs)

        # Write config for reference
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".json") as f:
            json.dump(self.config, f)
            f.flush()
            self.config_file = f.name

        # Login to required sites
        for site in self.config.get("sites", []):
            await self._ui_login(site, page)

        # Set geolocation
        geo = self.config.get("geolocation")
        if geo:
            await page.context.set_geolocation(geo)

        # Navigate to start URL
        start_url = self.config.get("start_url", "")
        if start_url:
            urls = start_url.split(" |AND| ")
            for i, url in enumerate(urls):
                await page.goto(url, timeout=60000)
                if i < len(urls) - 1:
                    page = await page.context.new_page()

        # Build goal
        goal = self.config.get("intent", "")
        goal += (
            f"\n\n(Note: if you want to visit other websites, check out the homepage "
            f"at {self.wa.home_url}. It has a list of websites you can visit. "
            f"{self.wa.home_url}/password.html lists all the account name and password "
            f"for the websites. You can use them to log in to the websites.)"
        )

        return goal, {}

    async def teardown(self) -> None:
        pass

    async def validate(
        self, page: playwright.async_api.Page, chat_messages: list
    ) -> Tuple[float, bool, str, dict]:
        """No-op — we compare answers externally, not via WebArena evaluator."""
        return 0.0, False, "", {}

    async def _ui_login(self, site: str, page: playwright.async_api.Page) -> None:
        """Login using BrowserGym's credentials, in a separate tab."""
        creds = self.wa.credentials.get(site)
        if not creds:
            return

        url = self.wa.urls.get(site, "")
        if not url:
            return

        login_page = await page.context.new_page()

        try:
            if site == "reddit":
                await login_page.goto(f"{url}", timeout=30000)
                await login_page.get_by_role("link", name="Log in").click()
                await login_page.get_by_label("Username").fill(creds["username"])
                await login_page.get_by_label("Password").fill(creds["password"])
                await login_page.get_by_role("button", name="Log in").click()

            elif site == "gitlab":
                await login_page.goto(f"{url}/users/sign_in", timeout=30000)
                await login_page.get_by_label("Username or email").fill(creds["username"])
                await login_page.get_by_label("Password").fill(creds["password"])
                await login_page.get_by_role("button", name="Sign in").click()

            elif site == "shopping":
                await login_page.goto(f"{url}/customer/account/login/", timeout=30000)
                await login_page.get_by_label("Email", exact=True).fill(creds["username"])
                await login_page.get_by_label("Password", exact=True).fill(creds["password"])
                await login_page.get_by_role("button", name="Sign In").click()

            elif site == "shopping_admin":
                await login_page.goto(f"{url}", timeout=60000)
                await login_page.get_by_label("Username").fill(creds["username"])
                await login_page.get_by_label("Password").fill(creds["password"])
                await login_page.get_by_role("button", name="Sign in").click()

            elif site in ("wikipedia", "map"):
                await login_page.goto(f"{url}", timeout=30000)

            await login_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            logger.warning(f"Login failed for {site}: {e}")
        finally:
            await login_page.close()
