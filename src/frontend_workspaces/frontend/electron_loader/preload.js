const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
    // Example: Send message to main process
    send: (channel, data) => {
        // Whitelist channels
        const validChannels = ['toMain'];
        if (validChannels.includes(channel)) {
            ipcRenderer.send(channel, data);
        }
    },
    // Example: Receive message from main process
    receive: (channel, func) => {
        const validChannels = ['fromMain'];
        if (!validChannels.includes(channel)) {
            throw new Error(`Unsupported IPC channel: ${channel}`);
        }

        const listener = (_event, ...args) => func(...args);
        ipcRenderer.on(channel, listener);

        return () => {
            ipcRenderer.removeListener(channel, listener);
        };
    },
    // Example: Invoke method (request-response pattern)
    invoke: (channel, data) => {
        const validChannels = ['getData'];
        if (validChannels.includes(channel)) {
            return ipcRenderer.invoke(channel, data);
        }
    }
});

// Expose safe Node.js APIs if needed
contextBridge.exposeInMainWorld('versions', {
    node: () => process.versions.node,
    chrome: () => process.versions.chrome,
    electron: () => process.versions.electron,
});

// Made with Bob
