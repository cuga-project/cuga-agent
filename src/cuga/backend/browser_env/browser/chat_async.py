# Copyright 2024 ServiceNow
# Modifications Copyright 2025 CUGA
# Licensed under the Apache License, Version 2.0

import base64
import logging
import re
import time
from importlib import resources
from pathlib import Path
from typing import Literal

import playwright.async_api
from browsergym.core.chat import chat_files

from cuga.backend.browser_env.browser.utils_async import _get_global_playwright_async
from loguru import logger as logguro_l

CHATBOX_DIR = resources.files(chat_files)

logger = logging.getLogger(__name__)


class Chat:
    def __init__(self, headless: bool, chat_size=(500, 800), record_video_dir=None, modern=True) -> None:
        self.headless = headless
        self.chat_size = chat_size
        self.record_video_dir = record_video_dir
        self.browser = None
        self.messages = []
        self.context = None
        self.page = None
        self.modern = modern
        self.recording_start_time = None

    async def init(self):
        pw: playwright.async_api.Playwright = await _get_global_playwright_async()
        self.browser = await pw.chromium.launch(
            channel="chrome",
            headless=self.headless,
            args=[f"--window-size={self.chat_size[0]},{self.chat_size[1]}"],
        )
        self.context = await self.browser.new_context(
            no_viewport=True,
            record_video_dir=Path(self.record_video_dir) / "chat_video" if self.record_video_dir else None,
            record_video_size=dict(width=self.chat_size[0], height=self.chat_size[1]),
        )
        self.page = await self.context.new_page()
        self.recording_start_time = time.time() if self.record_video_dir else None
        # setup the chat page
        await self.page.expose_function(
            "send_user_message", lambda msg: self._js_user_message_received_callback(msg=msg)
        )
        if self.modern:
            await self.page.set_content(get_chatbox_modern(CHATBOX_DIR))
        else:
            await self.page.set_content(get_chatbox_classic(CHATBOX_DIR))

    def _js_user_message_received_callback(self, msg: str):
        """Callback function for when a user message is received in the chatbox"""
        utc_time = time.time()
        self.messages.append({"role": "user", "timestamp": utc_time, "message": msg})
        # returning a list as JS doesnt like tuples
        return ["user", time.strftime("%H:%M", time.localtime(utc_time)), msg]

    async def add_message(
        self, role: Literal["user", "user_image", "assistant", "info", "infeasible"], msg: str
    ):
        logguro_l.debug(f"\nRole: {role}\n\nMsg:\n{msg}")
        """Add a message to the chatbox and update the page accordingly."""
        utc_time = time.time()
        if role not in ("user", "user_image", "assistant", "info", "infeasible"):
            raise ValueError(f"Invalid role: {role}")
        if role in ("user", "user_image", "assistant", "infeasible"):
            self.messages.append({"role": role, "timestamp": utc_time, "message": msg})
        timestamp = time.strftime("%H:%M:%S", time.localtime(utc_time))
        await self.page.evaluate(f"addChatMessage({repr(role)}, {repr(timestamp)}, {repr(msg)});")

    async def wait_for_user_message(self):
        logger.info("Waiting for message from user...")
        # reset flag
        await self.page.evaluate("USER_MESSAGE_RECEIVED = false;")
        # wait for flag to be raised
        await self.page.wait_for_function("USER_MESSAGE_RECEIVED", polling=100, timeout=0)
        logger.info("Message received.")

    async def close(self):
        await self.context.close()
        await self.browser.close()


def get_chatbox_modern(chatbox_dir) -> str:
    with open(chatbox_dir / "chatbox_modern.html", "r") as file:
        chatbox_html = file.read()

    return chatbox_html


def get_chatbox_classic(chatbox_dir) -> str:
    with open(chatbox_dir / "chatbox.html", "r") as file:
        chatbox_html = file.read()
    with open(chatbox_dir / "assistant.png", "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    assistant_image_url = f"data:image/png;base64,{image_base64}"
    chatbox_html = re.sub("<ASSISTANT_IMAGE_URL>", assistant_image_url, chatbox_html)
    return chatbox_html
