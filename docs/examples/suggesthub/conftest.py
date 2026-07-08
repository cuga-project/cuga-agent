def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run against a real LLM (requires valid API credentials in env)",
    )
