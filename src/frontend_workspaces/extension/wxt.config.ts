import path from "path";
import { ConfigEnv, defineConfig } from "wxt";
import { viteStaticCopy } from "vite-plugin-static-copy";
import { normalizePath } from "vite";

// See https://wxt.dev/api/config.html
export default defineConfig({
    srcDir: "src",
    modules: ["@wxt-dev/module-react", "@wxt-dev/webextension-polyfill"],

    manifest: ({ mode }) => {
        return {
            name: "CUGA",
            minimum_chrome_version: "116",
            icons: {
                "16": "icon/16.png",
                "48": "icon/48.png",
                "128": "icon/128.png"
            },
            content_security_policy: {
                extension_pages: "script-src 'self'; object-src 'self';",
            },
            host_permissions: ["*://*/*"],
            permissions: [
                "activeTab",
                "tabs",
                "identity",
                "sidePanel",
                "storage",
                "webNavigation",
                "debugger",
                "webRequest",
                "contextMenus",
                "scripting",
            ],
            action: {},
        };
    },
    vite: (configEnv: ConfigEnv) => {
        return {
            envPrefix: ["NL2UI", "VITE"],
            resolve: {
                alias: {
                    "@uiagent/shared": path.resolve(__dirname, "../shared/dist/index.js"),
                    "@agentic_chat": path.resolve(__dirname, "../agentic_chat/src"),
                },
            },
            plugins: [
                viteStaticCopy({
                    targets: [
                        {
                            src: normalizePath("../node_modules/.pnpm/@ibm+plex@6.4.1/node_modules/@ibm/plex/IBM-Plex-Sans"),
                            dest: normalizePath("fonts"),
                            // Keep IBM-Plex-Sans/ and its relative font paths in static-copy v4.
                            rename: { stripBase: 6 },
                        },
                    ],
                }),
            ],
        };
    },
});
