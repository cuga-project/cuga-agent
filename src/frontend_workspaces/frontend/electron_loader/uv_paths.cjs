const fs = require('fs');
const os = require('os');
const path = require('path');

function resolveUv({
    env = process.env,
    homedir = os.homedir(),
    platform = process.platform,
    accessSync = fs.accessSync,
} = {}) {
    if (env.UV_PATH) return env.UV_PATH;
    const uvBinary = platform === 'win32' ? 'uv.exe' : 'uv';
    // Common install locations when Electron strips PATH down to /usr/bin:/bin
    const candidates = [
        path.join(homedir, '.local', 'bin', uvBinary),
        '/usr/local/bin/uv',
        '/opt/homebrew/bin/uv',
        uvBinary,
    ];
    return (
        candidates.find((p) => {
            try {
                accessSync(p, fs.constants.X_OK);
                return true;
            } catch {
                return false;
            }
        }) || uvBinary
    );
}

function buildBackendPath({
    env = process.env,
    homedir = os.homedir(),
    delimiter = path.delimiter,
} = {}) {
    const entries = [
        path.join(homedir, '.local', 'bin'),
        '/usr/local/bin',
        '/opt/homebrew/bin',
        '/usr/bin',
        '/bin',
    ];
    if (env.PATH) {
        entries.push(env.PATH);
    }
    return entries.join(delimiter);
}

module.exports = {
    resolveUv,
    buildBackendPath,
};
