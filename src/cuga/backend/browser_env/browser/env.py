# Copyright 2024 ServiceNow
# Modifications Copyright 2025 CUGA
# Licensed under the Apache License, Version 2.0

import playwright
from browsergym.core.constants import BROWSERGYM_ID_ATTRIBUTE
from playwright.sync_api import sync_playwright


class BrowserEnvSimple:
    def __init__(self):
        # Initialize Playwright and the browser instance
        self.playwright = sync_playwright().start()
        self.playwright.selectors.set_test_id_attribute(BROWSERGYM_ID_ATTRIBUTE)

        self.browser = None
        self.context = None
        self.page = None
        self.feedback = []

    def _wait_dom_loaded(self):
        for page in self.context.pages:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except playwright.sync_api.Error:
                pass
            for frame in page.frames:
                try:
                    frame.wait_for_load_state("domcontentloaded", timeout=3000)
                except playwright.sync_api.Error:
                    pass

    def _active_page_check(self):
        # make sure there is always a page open
        # if all pages have been closed, create a new page
        if len(self.context.pages) == 0:
            self.page = self.context.new_page()

        # if the active page got closed, get the last active page from the history
        while self.page_history and (self.page.is_closed() or self.page not in self.context.pages):
            self.page_history.pop(self.page)  # remove active page from history
            self.page = list(self.page_history.keys())[
                -1
            ]  # set last active page as the active page (most recent)

        # active page should share the same browser context with the environment
        if self.page not in self.context.pages:
            raise RuntimeError(
                f"Unexpected: active page is not part of the browser context's open pages ({self.page})."
            )

        # active page should not be closed
        if self.page.is_closed():
            raise RuntimeError(f"Unexpected: active page has been closed ({self.page}).")

    def reset(self):
        self._wait_dom_loaded()

        # after the task's setup, the active page might have changed
        # perform a safety check
        # self._active_page_check()

        pass

    def start_browser(self, headless=False):
        # Launch the browser
        self.browser = self.playwright.chromium.launch(
            channel="chrome", headless=headless, args=["--no-sandbox"]
        )
        self.page = self.browser.new_page()
        self.context = self.browser.contexts[0]

    def navigate_to_page(self, url):
        # Navigate to the specified URL
        if self.page:
            self.page.goto(url, timeout=30000)
            print(f"Page title: {self.page.title()}")
        else:
            print("Browser not started. Call start_browser() first.")

    def close_browser(self):
        # Close the browser and stop Playwright
        if self.page:
            self.page.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
