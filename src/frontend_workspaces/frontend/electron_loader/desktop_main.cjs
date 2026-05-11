const { app, BrowserWindow, dialog } = require('electron');

// Disable the GPU sandbox and force hardware acceleration for smoother rendering
app.commandLine.appendSwitch('enable-accelerated-2d-canvas');
app.commandLine.appendSwitch('enable-gpu-rasterization');
app.disableHardwareAcceleration && void 0; // keep hardware accel enabled (default)
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const BACKEND_URL = 'http://127.0.0.1:7860';
const POLL_INTERVAL_MS = 1000;
const STARTUP_TIMEOUT_MS = 120_000;

let mainWindow = null;
let backendProcess = null;

function resolveUv() {
    if (process.env.UV_PATH) return process.env.UV_PATH;
    // Common install locations when Electron strips PATH down to /usr/bin:/bin
    const candidates = [
        '/Users/' + require('os').userInfo().username + '/.local/bin/uv',
        '/usr/local/bin/uv',
        '/opt/homebrew/bin/uv',
        'uv',
    ];
    const fs = require('fs');
    return candidates.find(p => { try { fs.accessSync(p, fs.constants.X_OK); return true; } catch { return false; } }) || 'uv';
}

function spawnBackend() {
    const uvPath = resolveUv();
    // __dirname is .../frontend/electron_loader — go up 4 levels to reach repo root
    const repoRoot = path.resolve(__dirname, '../../../..');
    backendProcess = spawn(uvPath, ['run', 'cuga', 'start', 'demo'], {
        cwd: repoRoot,
        env: {
            ...process.env,
            PATH: `/Users/${require('os').userInfo().username}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${process.env.PATH || ''}`,
        },
        stdio: 'inherit',
        detached: false,
    });

    backendProcess.on('error', (err) => {
        dialog.showErrorBox('Backend failed to start', err.message);
        app.quit();
    });

    backendProcess.on('exit', (code, signal) => {
        if (code !== 0 && code !== null && mainWindow) {
            dialog.showErrorBox('Backend exited unexpectedly', `Exit code: ${code}, signal: ${signal}`);
        }
    });
}

function pollUntilReady(url, timeoutMs) {
    return new Promise((resolve, reject) => {
        const deadline = Date.now() + timeoutMs;

        function attempt() {
            http.get(url, (res) => {
                res.resume();
                resolve();
            }).on('error', () => {
                if (Date.now() >= deadline) {
                    reject(new Error(`Backend at ${url} did not become ready within ${timeoutMs / 1000}s`));
                } else {
                    setTimeout(attempt, POLL_INTERVAL_MS);
                }
            });
        }

        attempt();
    });
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            nodeIntegration: false,
            contextIsolation: true,
            sandbox: true,
            backgroundThrottling: false,
        },
        show: false,
    });

    mainWindow.loadURL(BACKEND_URL);

    // Only open DevTools when explicitly requested via env var
    if (process.env.CUGA_DEVTOOLS === '1') {
        mainWindow.webContents.openDevTools({ mode: 'detach' });
    }

    // Show window only once the page has painted to avoid a flash/lag on load
    mainWindow.once('ready-to-show', () => mainWindow.show());

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

function killBackend() {
    if (backendProcess) {
        try {
            process.kill(-backendProcess.pid);
        } catch (_) {
            backendProcess.kill();
        }
        backendProcess = null;
    }
}

app.whenReady().then(async () => {
    spawnBackend();

    try {
        await pollUntilReady(BACKEND_URL, STARTUP_TIMEOUT_MS);
    } catch (err) {
        dialog.showErrorBox('Startup timeout', err.message);
        killBackend();
        app.quit();
        return;
    }

    createWindow();
});

app.on('window-all-closed', () => {
    killBackend();
    app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});

app.on('before-quit', () => {
    killBackend();
});
