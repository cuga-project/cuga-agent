const test = require('node:test');
const assert = require('node:assert/strict');
const os = require('os');
const path = require('path');

const { resolveUv, buildBackendPath } = require('./uv_paths.cjs');

test('resolveUv prefers UV_PATH when set', () => {
    const result = resolveUv({
        env: { UV_PATH: '/custom/bin/uv' },
        accessSync: () => {
            throw new Error('should not probe filesystem');
        },
    });
    assert.equal(result, '/custom/bin/uv');
});

test('resolveUv checks os.homedir()/.local/bin/uv instead of hardcoded /Users', () => {
    const customHome = path.join(os.tmpdir(), 'cuga-custom-home');
    const expected = path.join(customHome, '.local', 'bin', 'uv');
    const checked = [];
    const result = resolveUv({
        env: {},
        homedir: customHome,
        platform: 'linux',
        accessSync: (candidate) => {
            checked.push(candidate);
            if (candidate !== expected) {
                throw new Error('ENOENT');
            }
        },
    });
    assert.equal(result, expected);
    assert.equal(checked[0], expected);
});

test('resolveUv uses uv.exe on win32 and falls back when none executable', () => {
    const customHome = path.join(os.tmpdir(), 'cuga-win-home');
    const checked = [];
    const result = resolveUv({
        env: {},
        homedir: customHome,
        platform: 'win32',
        accessSync: (candidate) => {
            checked.push(candidate);
            throw new Error('ENOENT');
        },
    });
    assert.equal(checked[0], path.join(customHome, '.local', 'bin', 'uv.exe'));
    assert.equal(result, 'uv.exe');
});

test('buildBackendPath prepends homedir .local/bin and preserves existing PATH', () => {
    const customHome = path.join(os.tmpdir(), 'cuga-path-home');
    const out = buildBackendPath({
        env: { PATH: '/existing/bin' },
        homedir: customHome,
        delimiter: path.delimiter,
    });
    const parts = out.split(path.delimiter);
    assert.equal(parts[0], path.join(customHome, '.local', 'bin'));
    assert.equal(parts[parts.length - 1], '/existing/bin');
});
