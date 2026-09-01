from system_tests.e2e.base_test import BaseTestServerStream


class BaseCRMTestServerStream(BaseTestServerStream):
    _stability_stack = "crm"
    mode = "default"
    test_env_vars = {"DYNACONF_POLICY__ENABLED": "false"}
